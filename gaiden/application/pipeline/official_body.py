from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timezone as datetime_timezone
from pathlib import Path

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from gaiden.infrastructure import intake_storage, storage


class OfficialBodyError(RuntimeError):
    pass


@dataclass(frozen=True)
class OfficialBodyResult:
    path: Path
    snapshot_id: int
    sha256: str
    size: int
    no_op: bool
    operation_id: str | None = None


def canonical_path(edition) -> Path:
    return storage.editions_dir(edition.id) / "core" / "miolo_oficial.txt"


def versions_dir(edition) -> Path:
    return storage.editions_dir(edition.id) / "core" / "versions"


def _relative_storage_path(path: Path) -> str:
    root = storage.data_dir().resolve()
    resolved = path.resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise OfficialBodyError("Official body path escapes the configured storage root") from exc


def resolve_storage_path(relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise OfficialBodyError("Stored official body path must be relative")
    resolved = (storage.data_dir() / candidate).resolve(strict=False)
    try:
        resolved.relative_to(storage.data_dir().resolve())
    except ValueError as exc:
        raise OfficialBodyError("Stored official body path escapes the configured storage root") from exc
    return resolved


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validated_payload(payload: bytes) -> bytes:
    if not payload:
        raise OfficialBodyError("Official body cannot be empty")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OfficialBodyError("Official body must be valid UTF-8") from exc
    if not text.strip():
        raise OfficialBodyError("Official body cannot be blank")
    return payload


def active_snapshot(edition):
    OfficialBodySnapshot = apps.get_model("pipeline", "OfficialBodySnapshot")
    snapshots = list(
        OfficialBodySnapshot.objects.filter(edition_id=edition.id, is_active=True)[:2]
    )
    if len(snapshots) > 1:
        raise OfficialBodyError("More than one official body snapshot is active")
    return snapshots[0] if snapshots else None


def resolve_official_body(edition) -> Path | None:
    snapshot = active_snapshot(edition)
    if snapshot is None:
        return None
    version_path = resolve_storage_path(snapshot.relative_path)
    canonical = canonical_path(edition)
    if any(path.is_symlink() or not path.is_file() for path in (version_path, canonical)):
        return None
    if _digest(version_path.read_bytes()) != snapshot.sha256:
        return None
    if _digest(canonical.read_bytes()) != snapshot.sha256:
        return None
    return canonical


def _timestamp_token() -> str:
    return timezone.now().astimezone(datetime_timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def promote(
    edition,
    payload: bytes,
    *,
    provenance: str,
    source_stage: str,
    translation_job=None,
    input_sha256: str = "",
    actor: str = "system",
) -> OfficialBodyResult:
    payload = _validated_payload(payload)
    digest = _digest(payload)
    canonical = canonical_path(edition)
    if canonical.exists() and (canonical.is_symlink() or not canonical.is_file()):
        raise OfficialBodyError("Official body destination is not a regular file")

    OfficialBodyPromotion = apps.get_model("pipeline", "OfficialBodyPromotion")
    OfficialBodySnapshot = apps.get_model("pipeline", "OfficialBodySnapshot")
    EditionPipeline = apps.get_model("editorial", "EditionPipeline")

    with transaction.atomic():
        locked_edition = type(edition).objects.select_for_update().get(pk=edition.pk)
        pipeline_state, _ = EditionPipeline.objects.select_for_update().get_or_create(
            edition=locked_edition
        )
        previous = (
            OfficialBodySnapshot.objects.select_for_update()
            .filter(edition=locked_edition, is_active=True)
            .first()
        )
        if previous is not None and previous.sha256 == digest:
            current = resolve_official_body(locked_edition)
            if current is not None:
                if translation_job is not None and translation_job.status != translation_job.STATUS_COMPLETED:
                    type(translation_job).objects.filter(pk=translation_job.pk).update(
                        status=translation_job.STATUS_COMPLETED,
                        return_sha256=digest,
                        updated_at=timezone.now(),
                    )
                return OfficialBodyResult(current, previous.id, digest, len(payload), True)

        previous_canonical_sha = ""
        if canonical.is_file() and not canonical.is_symlink():
            previous_canonical_sha = _digest(canonical.read_bytes())
        operation = OfficialBodyPromotion.objects.create(
            edition=locked_edition,
            translation_job=translation_job,
            previous_snapshot=previous,
            input_sha256=input_sha256,
            return_sha256=digest,
            previous_canonical_sha256=previous_canonical_sha,
        )
        staged = storage.editions_dir(locked_edition.id) / "core" / f".promotion_{operation.operation_id}.staged"
        intake_storage.atomic_write_bytes(staged, payload)
        operation.staged_path = _relative_storage_path(staged)
        operation.state = operation.FILE_STAGED
        operation.save(update_fields=["staged_path", "state", "updated_at"])

        version = versions_dir(locked_edition) / f"{_timestamp_token()}_{digest}.txt"
        intake_storage.atomic_write_bytes(version, payload)
        now = timezone.now()
        OfficialBodySnapshot.objects.filter(
            edition=locked_edition,
            is_active=True,
        ).update(is_active=False, superseded_at=now)
        snapshot, created = OfficialBodySnapshot.objects.get_or_create(
            edition=locked_edition,
            sha256=digest,
            defaults={
                "translation_job": translation_job,
                "size": len(payload),
                "relative_path": _relative_storage_path(version),
                "provenance": provenance,
                "source_stage": source_stage,
            },
        )
        if not created:
            stored_version = resolve_storage_path(snapshot.relative_path)
            if not stored_version.is_file() or _digest(stored_version.read_bytes()) != digest:
                raise OfficialBodyError("Existing official snapshot is missing or corrupt")
        snapshot.is_active = True
        snapshot.activated_at = now
        snapshot.superseded_at = None
        snapshot.save(update_fields=["is_active", "activated_at", "superseded_at"])

        pipeline_state.core_last_txt_path = _relative_storage_path(canonical)
        pipeline_state.last_log = (
            f"{now.isoformat()} :: OFFICIAL_BODY_DB_COMMITTED :: "
            f"provenance={provenance} sha256={digest} actor={actor}"
        )
        pipeline_state.save(update_fields=["core_last_txt_path", "last_log"])
        operation.new_snapshot = snapshot
        operation.state = operation.DB_COMMITTED
        operation.save(update_fields=["new_snapshot", "state", "updated_at"])

    intake_storage.atomic_write_bytes(canonical, payload, overwrite=True)

    with transaction.atomic():
        operation = OfficialBodyPromotion.objects.select_for_update().get(pk=operation.pk)
        operation.state = operation.CANONICAL_PUBLISHED
        operation.save(update_fields=["state", "updated_at"])
        if translation_job is not None:
            type(translation_job).objects.filter(pk=translation_job.pk).update(
                status=translation_job.STATUS_COMPLETED,
                return_sha256=digest,
                updated_at=timezone.now(),
            )
        staged.unlink(missing_ok=True)
        operation.state = operation.COMPLETED
        operation.completed_at = timezone.now()
        operation.error = ""
        operation.save(update_fields=["state", "completed_at", "error", "updated_at"])

    return OfficialBodyResult(
        canonical,
        snapshot.id,
        digest,
        len(payload),
        False,
        str(operation.operation_id),
    )


def promote_internal_polish(edition, source_path: Path, *, actor: str = "system") -> OfficialBodyResult:
    source = Path(source_path)
    if source.is_symlink() or not source.is_file():
        raise OfficialBodyError("Validated internal polish output is unavailable")
    return promote(
        edition,
        source.read_bytes(),
        provenance="internal_polish",
        source_stage="polish",
        actor=actor,
    )


def reconcile_operation(operation) -> str:
    OfficialBodyPromotion = apps.get_model("pipeline", "OfficialBodyPromotion")
    with transaction.atomic():
        locked = OfficialBodyPromotion.objects.select_for_update().get(pk=operation.pk)
        if locked.state == locked.COMPLETED:
            return "already_completed"
        if locked.state not in {locked.DB_COMMITTED, locked.CANONICAL_PUBLISHED}:
            locked.state = locked.FAILED
            locked.error = "Automatic reconciliation requires a committed snapshot"
            locked.save(update_fields=["state", "error", "updated_at"])
            return "marked_failed"
        if locked.new_snapshot is None:
            locked.state = locked.FAILED
            locked.error = "Committed promotion has no snapshot"
            locked.save(update_fields=["state", "error", "updated_at"])
            return "marked_failed"
        version = resolve_storage_path(locked.new_snapshot.relative_path)
        if version.is_symlink() or not version.is_file():
            locked.state = locked.FAILED
            locked.error = "Committed snapshot file is missing"
            locked.save(update_fields=["state", "error", "updated_at"])
            return "marked_failed"
        payload = version.read_bytes()
        if _digest(payload) != locked.return_sha256:
            locked.state = locked.FAILED
            locked.error = "Committed snapshot SHA mismatch"
            locked.save(update_fields=["state", "error", "updated_at"])
            return "marked_failed"

    intake_storage.atomic_write_bytes(canonical_path(locked.edition), payload, overwrite=True)
    staged = resolve_storage_path(locked.staged_path) if locked.staged_path else None
    if staged is not None:
        staged.unlink(missing_ok=True)
    with transaction.atomic():
        locked = OfficialBodyPromotion.objects.select_for_update().get(pk=locked.pk)
        locked.state = locked.COMPLETED
        locked.completed_at = timezone.now()
        locked.error = ""
        locked.save(update_fields=["state", "completed_at", "error", "updated_at"])
        if locked.translation_job_id:
            type(locked.translation_job).objects.filter(pk=locked.translation_job_id).update(
                status="COMPLETED",
                return_sha256=locked.return_sha256,
                updated_at=timezone.now(),
            )
    return "completed"
