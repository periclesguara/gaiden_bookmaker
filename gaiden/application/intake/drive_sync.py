from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path

from django.db import transaction

from gaiden.infrastructure import intake_storage
from gaiden.infrastructure.intake_drive import RcloneClient

from gaiden.domain.intake import IntakeState

from .ingestion import (
    ACCEPTED_SUFFIXES,
    IGNORED_IMAGE_SUFFIXES,
    discover_item,
    ingest_path,
    store_downloaded_bytes,
)
from .reconciliation import (
    IntakeArtifactConflict,
    adopt_existing_artifact,
    inspect_original_artifact,
)
from .workflow import transition_item


def discover_drive_folder(batch, relative_folder: str, *, client=None) -> dict:
    client = client or RcloneClient()
    client.check_available()
    files = client.list_files(relative_folder)
    report = {
        "folder": relative_folder,
        "files": [],
        "format_counts": {},
        "discovered": [],
        "ignored": [],
        "existing": [],
        "errors": [],
    }
    for drive_file in files:
        suffix = Path(drive_file.name).suffix.lower()
        row = {
            "filename": drive_file.name,
            "extension": suffix.lstrip(".").upper() or "—",
            "size": drive_file.size,
            "remote_id": drive_file.file_id,
            "compatible": suffix in ACCEPTED_SUFFIXES,
            "item_id": None,
            "state": "DISCOVERED" if suffix in ACCEPTED_SUFFIXES else "Ignorado nesta etapa",
        }
        if suffix in IGNORED_IMAGE_SUFFIXES:
            report["ignored"].append(
                {"filename": drive_file.name, "reason": "image_not_processed_in_v1"}
            )
            report["files"].append(row)
            continue
        if suffix not in ACCEPTED_SUFFIXES:
            row["state"] = "Formato não compatível"
            report["ignored"].append({"filename": drive_file.name, "reason": "unsupported_format"})
            report["files"].append(row)
            continue
        existing = None
        if drive_file.file_id:
            existing = batch.items.filter(drive_file_id=drive_file.file_id).first()
        if existing is None:
            existing = batch.items.filter(
                source_filename=drive_file.name,
                source_size=drive_file.size,
            ).first()
        if existing is not None:
            report["existing"].append({"filename": drive_file.name, "item_id": existing.id})
            row["item_id"] = existing.id
            row["state"] = existing.status
            report["files"].append(row)
            continue
        try:
            item = discover_item(
                batch,
                drive_file.name,
                source_size=drive_file.size,
                drive_file_id=drive_file.file_id,
            )
            report["discovered"].append({"filename": drive_file.name, "item_id": item.id})
            row["item_id"] = item.id
            row["state"] = item.status
        except Exception as exc:
            row["state"] = "ERROR"
            report["errors"].append({"filename": drive_file.name, "error": str(exc)[:500]})
        report["files"].append(row)
    report["format_counts"] = dict(
        sorted(Counter(row["extension"] for row in report["files"]).items())
    )
    intake_storage.ensure_batch_layout(batch.code, batch.source_language)
    intake_storage.atomic_write_json(
        intake_storage.drive_audit_path(batch.code, batch.source_language), report, overwrite=True
    )
    return report


def download_drive_item(item, *, client=None) -> dict:
    try:
        with transaction.atomic():
            locked = (
                type(item).objects.select_for_update().select_related("batch").get(pk=item.pk)
            )
            inspection = inspect_original_artifact(locked)
            if inspection.exists:
                return adopt_existing_artifact(locked, inspection)
            if locked.status not in {
                IntakeState.DISCOVERED.value,
                IntakeState.DOWNLOADING.value,
                IntakeState.DOWNLOADED.value,
                IntakeState.FAILED.value,
            }:
                raise ValueError("Item is not eligible for download recovery")
            if not locked.batch.drive_relative_path:
                raise ValueError("Drive folder is not configured for this batch")
            locked.status = IntakeState.DOWNLOADING.value
            locked.last_error = ""
            locked.save(update_fields=["status", "last_error", "updated_at"])
            client = client or RcloneClient()
            client.check_available()
            files = client.list_files(locked.batch.drive_relative_path)
            drive_file = next(
                (
                    row
                    for row in files
                    if (locked.drive_file_id and row.file_id == locked.drive_file_id)
                    or (not locked.drive_file_id and row.name == locked.source_filename)
                ),
                None,
            )
            if drive_file is None or drive_file.name != locked.source_filename:
                raise FileNotFoundError("Discovered Drive file is no longer available")
            with tempfile.TemporaryDirectory(prefix="gaiden-intake-drive-") as temporary:
                destination = Path(temporary) / Path(drive_file.name).name
                client.download_file(locked.batch.drive_relative_path, drive_file, destination)
                return store_downloaded_bytes(
                    locked,
                    destination.read_bytes(),
                    drive_file_id=drive_file.file_id,
                )
    except IntakeArtifactConflict as exc:
        with transaction.atomic():
            locked = type(item).objects.select_for_update().get(pk=item.pk)
            locked.status = IntakeState.FAILED.value
            locked.last_error = str(exc)[:500]
            locked.save(update_fields=["status", "last_error", "updated_at"])
        raise
    except Exception as exc:
        with transaction.atomic():
            locked = type(item).objects.select_for_update().get(pk=item.pk)
            if locked.status != IntakeState.DOWNLOADED.value:
                locked.status = IntakeState.FAILED.value
                locked.last_error = str(exc)[:500]
                locked.save(update_fields=["status", "last_error", "updated_at"])
        raise


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
