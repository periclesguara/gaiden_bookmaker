from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from django.db import transaction
from django.utils import timezone

from gaiden.domain.intake import IntakeState
from gaiden.infrastructure import intake_storage


ARTIFACT_CONFLICT = "ARTIFACT_CONFLICT"
RECOVERABLE_DOWNLOAD_STATES = {
    IntakeState.DISCOVERED.value,
    IntakeState.DOWNLOADING.value,
    IntakeState.DOWNLOADED.value,
    IntakeState.FAILED.value,
}
COPY_SUFFIX = re.compile(r"\s*\(\d+\)(?=\.[^.]+$)", re.IGNORECASE)


class IntakeArtifactConflict(ValueError):
    pass


@dataclass(frozen=True)
class ArtifactInspection:
    item_id: int
    path: Path
    relative_path: str
    exists: bool
    valid: bool
    size: int | None = None
    sha256: str = ""
    reason: str = ""


def expected_original_path(item) -> Path:
    suffix = Path(item.source_filename).suffix.lower()
    return intake_storage.original_path(
        item.batch.code,
        item.batch.source_language,
        item.order_index,
        suffix,
    )


def inspect_original_artifact(item) -> ArtifactInspection:
    path = expected_original_path(item)
    if path.is_symlink():
        return _invalid(item, path, "symlink is not allowed")
    root = intake_storage.intake_root().resolve()
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        return _invalid(item, path, "path outside data/intake")
    if not path.exists():
        return ArtifactInspection(
            item_id=item.id,
            path=path,
            relative_path=intake_storage.relative_storage_path(path),
            exists=False,
            valid=False,
            reason="artifact not found",
        )
    if not path.is_file():
        return _invalid(item, path, "artifact is not a regular file")
    size = path.stat().st_size
    if size != item.source_size:
        return _invalid(
            item,
            path,
            f"size mismatch: expected {item.source_size}, found {size}",
            size=size,
        )
    if Path(item.source_filename).suffix.lower() == ".epub":
        try:
            with ZipFile(path) as archive:
                corrupt_member = archive.testzip()
        except (BadZipFile, OSError) as exc:
            return _invalid(item, path, f"invalid EPUB ZIP: {type(exc).__name__}", size=size)
        if corrupt_member:
            return _invalid(item, path, f"corrupt EPUB member: {corrupt_member}", size=size)
    digest = _sha256(path)
    return ArtifactInspection(
        item_id=item.id,
        path=path,
        relative_path=intake_storage.relative_storage_path(path),
        exists=True,
        valid=True,
        size=size,
        sha256=digest,
    )


def adopt_existing_artifact(item, inspection: ArtifactInspection | None = None) -> dict | None:
    inspection = inspection or inspect_original_artifact(item)
    if not inspection.exists:
        return None
    if not inspection.valid:
        _mark_conflict(item, inspection.reason)
        raise IntakeArtifactConflict(f"{ARTIFACT_CONFLICT}: {inspection.reason}")
    item.original_path = inspection.relative_path
    item.source_sha256 = inspection.sha256
    if item.status in RECOVERABLE_DOWNLOAD_STATES:
        item.status = IntakeState.DOWNLOADED.value
    item.last_error = ""
    item.save(
        update_fields=["original_path", "source_sha256", "status", "last_error", "updated_at"]
    )
    duplicate_of_id = update_duplicate_group(item.batch, inspection.sha256)
    item.refresh_from_db()
    return {
        "item": item,
        "duplicate": item.duplicate_of_id is not None,
        "duplicate_of_id": duplicate_of_id,
        "adopted": True,
        "no_op": True,
        "root": intake_storage.batch_root(item.batch.code, item.batch.source_language),
    }


def update_duplicate_group(batch, digest: str) -> int | None:
    if not digest:
        return None
    items = list(
        batch.items.select_for_update()
        .filter(source_sha256=digest)
        .order_by("order_index", "id")
    )
    if not items:
        return None
    canonical = min(items, key=_canonical_sort_key)
    now = timezone.now()
    for candidate in items:
        duplicate_of_id = None if candidate.id == canonical.id else canonical.id
        if candidate.duplicate_of_id != duplicate_of_id:
            type(candidate).objects.filter(pk=candidate.pk).update(
                duplicate_of_id=duplicate_of_id,
                updated_at=now,
            )
    return canonical.id


def reconcile_batch_downloads(
    batch,
    *,
    dry_run: bool = False,
    item_ids: set[int] | None = None,
) -> dict:
    if dry_run:
        items = list(batch.items.select_related("batch").order_by("order_index", "id"))
        return _build_reconciliation_report(items, apply=False, item_ids=item_ids)
    with transaction.atomic():
        locked_batch = type(batch).objects.select_for_update().get(pk=batch.pk)
        items = list(
            locked_batch.items.select_for_update().select_related("batch").order_by("order_index", "id")
        )
        return _build_reconciliation_report(items, apply=True, item_ids=item_ids)


def reconcile_item_download(item, *, dry_run: bool = False) -> dict:
    return reconcile_batch_downloads(
        item.batch,
        dry_run=dry_run,
        item_ids={item.id},
    )


def _build_reconciliation_report(
    items: list,
    *,
    apply: bool,
    item_ids: set[int] | None,
) -> dict:
    known_ids = {item.id for item in items}
    selected_ids = known_ids if item_ids is None else set(item_ids)
    if not selected_ids.issubset(known_ids):
        raise ValueError("Reconciliation item does not belong to this batch")
    selected_items = [item for item in items if item.id in selected_ids]
    inspections = {item.id: inspect_original_artifact(item) for item in items}
    canonical_by_digest: dict[str, object] = {}
    valid_groups: dict[str, list] = {}
    for item in items:
        inspection = inspections[item.id]
        if inspection.valid:
            valid_groups.setdefault(inspection.sha256, []).append(item)
    for digest, group in valid_groups.items():
        canonical_by_digest[digest] = min(group, key=_canonical_sort_key)

    report = {
        "dry_run": not apply,
        "batch_id": items[0].batch_id if items else None,
        "adoptable": [],
        "interrupted": [],
        "conflicts": [],
        "duplicates": [],
        "hashes": [],
        "unchanged": [],
    }
    now = timezone.now()
    for item in selected_items:
        inspection = inspections[item.id]
        base = {
            "item_id": item.id,
            "order_index": item.order_index,
            "filename": item.source_filename,
            "path": inspection.relative_path,
        }
        if inspection.valid:
            canonical = canonical_by_digest[inspection.sha256]
            duplicate_of_id = None if canonical.id == item.id else canonical.id
            report["hashes"].append({**base, "sha256": inspection.sha256})
            if duplicate_of_id:
                report["duplicates"].append(
                    {
                        **base,
                        "sha256": inspection.sha256,
                        "duplicate_of_id": duplicate_of_id,
                        "duplicate_of_order_index": canonical.order_index,
                    }
                )
            target_status = (
                IntakeState.DOWNLOADED.value
                if item.status in RECOVERABLE_DOWNLOAD_STATES
                else item.status
            )
            needs_adoption = any(
                (
                    item.original_path != inspection.relative_path,
                    item.source_sha256 != inspection.sha256,
                    item.status != target_status,
                    bool(item.last_error),
                )
            )
            if needs_adoption:
                report["adoptable"].append(
                    {**base, "sha256": inspection.sha256, "from_status": item.status, "to_status": target_status}
                )
            else:
                report["unchanged"].append({**base, "sha256": inspection.sha256})
            duplicate_changed = item.duplicate_of_id != duplicate_of_id
            if apply and (needs_adoption or duplicate_changed):
                type(item).objects.filter(pk=item.pk).update(
                    original_path=inspection.relative_path,
                    source_sha256=inspection.sha256,
                    status=target_status,
                    last_error="",
                    duplicate_of_id=duplicate_of_id,
                    updated_at=now,
                )
        elif not inspection.exists and item.status == IntakeState.DOWNLOADING.value:
            report["interrupted"].append({**base, "reason": "download interrompido"})
            needs_interruption_reset = (
                item.status != IntakeState.DISCOVERED.value
                or item.last_error != "download interrompido"
                or item.duplicate_of_id is not None
            )
            if apply and needs_interruption_reset:
                type(item).objects.filter(pk=item.pk).update(
                    status=IntakeState.DISCOVERED.value,
                    last_error="download interrompido",
                    duplicate_of=None,
                    updated_at=now,
                )
        elif inspection.exists:
            report["conflicts"].append({**base, "reason": inspection.reason})
            conflict_error = f"{ARTIFACT_CONFLICT}: {inspection.reason}"
            needs_conflict_update = (
                item.status != IntakeState.FAILED.value
                or item.last_error != conflict_error
                or item.duplicate_of_id is not None
            )
            if apply and needs_conflict_update:
                type(item).objects.filter(pk=item.pk).update(
                    status=IntakeState.FAILED.value,
                    last_error=conflict_error,
                    duplicate_of=None,
                    updated_at=now,
                )
        else:
            report["unchanged"].append({**base, "reason": inspection.reason})
    return report


def _mark_conflict(item, reason: str) -> None:
    item.status = IntakeState.FAILED.value
    item.last_error = f"{ARTIFACT_CONFLICT}: {reason}"
    item.save(update_fields=["status", "last_error", "updated_at"])


def _canonical_sort_key(item) -> tuple[int, int, int]:
    has_copy_suffix = bool(COPY_SUFFIX.search(item.source_filename))
    return (int(has_copy_suffix), item.order_index, item.id)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _invalid(item, path: Path, reason: str, *, size: int | None = None) -> ArtifactInspection:
    try:
        relative_path = intake_storage.relative_storage_path(path)
    except intake_storage.IntakeStorageError:
        relative_path = str(path)
    return ArtifactInspection(
        item_id=item.id,
        path=path,
        relative_path=relative_path,
        exists=path.exists() or path.is_symlink(),
        valid=False,
        size=size,
        reason=reason,
    )
