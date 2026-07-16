from __future__ import annotations

import tempfile
from pathlib import Path

from gaiden.infrastructure import intake_storage
from gaiden.infrastructure.intake_drive import RcloneClient

from gaiden.domain.intake import IntakeState

from .ingestion import ACCEPTED_SUFFIXES, IGNORED_IMAGE_SUFFIXES, discover_item, ingest_path
from .workflow import transition_item


def synchronize_drive_folder(batch, relative_folder: str, *, client=None, converter=None) -> dict:
    client = client or RcloneClient()
    client.check_available()
    files = client.list_files(relative_folder)
    report = {"folder": relative_folder, "imported": [], "ignored": [], "errors": []}
    for drive_file in files:
        suffix = Path(drive_file.name).suffix.lower()
        if suffix in IGNORED_IMAGE_SUFFIXES:
            report["ignored"].append(
                {"filename": drive_file.name, "reason": "image_not_processed_in_v1"}
            )
            continue
        if suffix not in ACCEPTED_SUFFIXES:
            report["ignored"].append({"filename": drive_file.name, "reason": "unsupported_format"})
            continue
        item = discover_item(
            batch,
            drive_file.name,
            source_size=drive_file.size,
            drive_file_id=drive_file.file_id,
        )
        try:
            transition_item(item, IntakeState.DOWNLOADING)
            with tempfile.TemporaryDirectory(prefix="gaiden-intake-drive-") as temporary:
                destination = Path(temporary) / Path(drive_file.name).name
                client.download_file(relative_folder, drive_file, destination)
                result = ingest_path(
                    batch,
                    destination,
                    drive_file_id=drive_file.file_id,
                    converter=converter,
                    item=item,
                )
            report["imported"].append(
                {"filename": drive_file.name, "item_id": result["item"].id, "duplicate": result["duplicate"]}
            )
        except Exception as exc:
            if item.status != IntakeState.FAILED.value:
                try:
                    transition_item(item, IntakeState.FAILED, error=str(exc))
                except Exception:
                    pass
            report["errors"].append({"filename": drive_file.name, "error": str(exc)[:500]})
    intake_storage.ensure_batch_layout(batch.code, batch.source_language)
    intake_storage.atomic_write_json(
        intake_storage.drive_audit_path(batch.code, batch.source_language), report, overwrite=True
    )
    return report
