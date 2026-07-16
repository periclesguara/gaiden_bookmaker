from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path
from typing import Iterable, Protocol

from django.db import transaction
from django.db.models import Max

from gaiden.domain.intake import IntakeState
from gaiden.infrastructure import intake_storage
from gaiden.infrastructure.converters.markitdown_adapter import MarkItDownAdapter

from .reconciliation import (
    ARTIFACT_CONFLICT,
    IntakeArtifactConflict,
    adopt_existing_artifact,
    inspect_original_artifact,
    update_duplicate_group,
)
from .workflow import transition_item


ACCEPTED_SUFFIXES = {".epub", ".txt", ".html", ".htm"}
IGNORED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
CLEAR_METADATA_LINES = (
    re.compile(r"^copyright(?:\s+©)?\b", re.IGNORECASE),
    re.compile(r"^all rights reserved\.?$", re.IGNORECASE),
    re.compile(r"^(?:published|printed|distributed) by\b", re.IGNORECASE),
    re.compile(r"^isbn(?:-1[03])?\s*[: ]", re.IGNORECASE),
    re.compile(r"^(?:previous|next|back to top|table of contents)$", re.IGNORECASE),
)


class Converter(Protocol):
    def convert_to_markdown(self, source_path: str | Path) -> str:
        ...


def _validated_filename(filename: str) -> str:
    candidate = Path(filename or "")
    if (
        not filename
        or candidate.is_absolute()
        or candidate.name != filename
        or ".." in candidate.parts
        or "\\" in filename
        or ":" in filename
    ):
        raise intake_storage.IntakeStorageError(f"Unsafe source filename: {filename!r}")
    return candidate.name


def clean_extracted_text(text: str) -> tuple[str, list[str], list[str]]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    kept: list[str] = []
    removed: list[str] = []
    warnings: list[str] = []
    for line in normalized.splitlines():
        stripped = line.strip().strip("#*").strip()
        if stripped and any(pattern.search(stripped) for pattern in CLEAR_METADATA_LINES):
            removed.append(line)
            continue
        if "copyright" in stripped.lower() and len(stripped.split()) > 12:
            warnings.append(f"Ambiguous copyright reference preserved: {stripped[:120]}")
        kept.append(line.rstrip())
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned).strip()
    if not cleaned:
        raise ValueError("Cleaning produced an empty text")
    return cleaned + "\n", removed, warnings


def ingest_uploaded_file(batch, uploaded_file, *, converter: Converter | None = None) -> dict:
    return ingest_bytes(
        batch,
        uploaded_file.name,
        b"".join(uploaded_file.chunks()),
        converter=converter,
    )


def ingest_path(
    batch,
    source_path: Path,
    *,
    drive_file_id: str = "",
    converter: Converter | None = None,
    item=None,
) -> dict:
    source = Path(source_path)
    if source.is_symlink():
        raise intake_storage.IntakeStorageError(f"Symlink input is not allowed: {source}")
    if not source.is_file():
        raise FileNotFoundError(f"Intake source is not a regular file: {source}")
    payload = source.read_bytes()
    return ingest_bytes(
        batch,
        source.name,
        payload,
        drive_file_id=drive_file_id,
        converter=converter,
        item=item,
    )


def discover_item(batch, filename: str, *, source_size: int = 0, drive_file_id: str = ""):
    safe_name = _validated_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        raise ValueError(f"Unsupported intake format: {suffix or '(none)'}")
    with transaction.atomic():
        locked_batch = type(batch).objects.select_for_update().get(pk=batch.pk)
        next_order = (locked_batch.items.aggregate(value=Max("order_index"))["value"] or 0) + 1
        return locked_batch.items.create(
            order_index=next_order,
            drive_file_id=drive_file_id,
            source_filename=safe_name,
            source_format=suffix.lstrip("."),
            source_size=max(0, source_size),
            suggested_title=Path(safe_name).stem.replace("_", " ").strip(),
        )


def ingest_bytes(
    batch,
    filename: str,
    payload: bytes,
    *,
    drive_file_id: str = "",
    converter: Converter | None = None,
    item=None,
) -> dict:
    safe_name = _validated_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix in IGNORED_IMAGE_SUFFIXES:
        return {"ignored": True, "filename": safe_name, "reason": "image_not_processed_in_v1"}
    if suffix not in ACCEPTED_SUFFIXES:
        raise ValueError(f"Unsupported intake format: {suffix or '(none)'}")
    if not payload:
        raise ValueError("Empty intake source is not allowed")

    if item is None:
        item = discover_item(batch, safe_name, source_size=len(payload), drive_file_id=drive_file_id)
    elif item.batch_id != batch.id or item.source_filename != safe_name:
        raise ValueError("Discovered item does not match downloaded source")

    try:
        download_result = store_downloaded_bytes(
            item,
            payload,
            drive_file_id=drive_file_id,
        )
        item = download_result["item"]
        if download_result["duplicate"]:
            return {
                "ignored": False,
                "item": item,
                "duplicate": True,
                "audit": None,
                "root": download_result["root"],
            }
        clean_result = clean_downloaded_item(item, converter=converter)
        return {
            "ignored": False,
            "item": item,
            "duplicate": download_result["duplicate"],
            "audit": clean_result["audit"],
            "root": download_result["root"],
        }
    except Exception as exc:
        if item.status != IntakeState.FAILED.value:
            try:
                transition_item(item, IntakeState.FAILED, error=str(exc))
            except Exception:
                item.status = IntakeState.FAILED.value
                item.last_error = str(exc)
                item.save(update_fields=["status", "last_error", "updated_at"])
        raise


def ingest_many(batch, files: Iterable, *, converter: Converter | None = None) -> list[dict]:
    return [ingest_uploaded_file(batch, uploaded, converter=converter) for uploaded in files]


def store_uploaded_files(batch, files: Iterable) -> list[dict]:
    results: list[dict] = []
    for uploaded in files:
        item = None
        try:
            safe_name = _validated_filename(uploaded.name)
            suffix = Path(safe_name).suffix.lower()
            if suffix in IGNORED_IMAGE_SUFFIXES:
                results.append(
                    {"ignored": True, "filename": safe_name, "reason": "image_not_processed_in_v1"}
                )
                continue
            if suffix not in ACCEPTED_SUFFIXES:
                results.append(
                    {"ignored": True, "filename": safe_name, "reason": "unsupported_format"}
                )
                continue
            payload = b"".join(uploaded.chunks())
            item = discover_item(batch, safe_name, source_size=len(payload))
            result = store_downloaded_bytes(item, payload)
            results.append({"ignored": False, "item": item, **result})
        except Exception as exc:
            if item is not None and item.status != IntakeState.FAILED.value:
                try:
                    transition_item(item, IntakeState.FAILED, error=str(exc))
                except Exception:
                    pass
            results.append(
                {
                    "ignored": False,
                    "item": item,
                    "filename": getattr(uploaded, "name", ""),
                    "error": str(exc)[:500],
                }
            )
    return results


def store_downloaded_bytes(item, payload: bytes, *, drive_file_id: str = "") -> dict:
    if not payload:
        raise ValueError("Empty intake source is not allowed")
    try:
        return _store_downloaded_bytes_locked(item, payload, drive_file_id=drive_file_id)
    except IntakeArtifactConflict as exc:
        with transaction.atomic():
            locked = type(item).objects.select_for_update().get(pk=item.pk)
            locked.status = IntakeState.FAILED.value
            locked.last_error = str(exc)[:500]
            locked.save(update_fields=["status", "last_error", "updated_at"])
        raise


def _store_downloaded_bytes_locked(item, payload: bytes, *, drive_file_id: str = "") -> dict:
    with transaction.atomic():
        locked = (
            type(item).objects.select_for_update().select_related("batch").get(pk=item.pk)
        )
        suffix = Path(locked.source_filename).suffix.lower()
        if suffix not in ACCEPTED_SUFFIXES:
            raise ValueError(f"Unsupported intake format: {suffix or '(none)'}")
        digest = hashlib.sha256(payload).hexdigest()
        inspection = inspect_original_artifact(locked)
        if inspection.exists:
            if inspection.valid and inspection.sha256 == digest:
                locked.drive_file_id = drive_file_id or locked.drive_file_id
                locked.save(update_fields=["drive_file_id", "updated_at"])
                return adopt_existing_artifact(locked, inspection)
            reason = inspection.reason or "existing artifact differs from downloaded content"
            locked.status = IntakeState.FAILED.value
            locked.last_error = f"{ARTIFACT_CONFLICT}: {reason}"
            locked.save(update_fields=["status", "last_error", "updated_at"])
            raise IntakeArtifactConflict(locked.last_error)
        if locked.status not in {
            IntakeState.DISCOVERED.value,
            IntakeState.DOWNLOADING.value,
            IntakeState.DOWNLOADED.value,
            IntakeState.FAILED.value,
        }:
            raise ValueError("Item is not eligible for download recovery")
        locked.status = IntakeState.DOWNLOADING.value
        locked.last_error = ""
        locked.save(update_fields=["status", "last_error", "updated_at"])
        root = intake_storage.ensure_batch_layout(
            locked.batch.code,
            locked.batch.source_language,
        )
        destination = intake_storage.original_path(
            locked.batch.code,
            locked.batch.source_language,
            locked.order_index,
            suffix,
        )
        intake_storage.atomic_write_bytes(destination, payload)
        locked.source_size = len(payload)
        locked.source_sha256 = digest
        locked.drive_file_id = drive_file_id or locked.drive_file_id
        locked.original_path = intake_storage.relative_storage_path(destination)
        locked.status = IntakeState.DOWNLOADED.value
        locked.last_error = ""
        locked.save(
            update_fields=[
                "source_size",
                "source_sha256",
                "drive_file_id",
                "original_path",
                "status",
                "last_error",
                "updated_at",
            ]
        )
        update_duplicate_group(locked.batch, digest)
        locked.refresh_from_db()
        return {
            "item": locked,
            "duplicate": locked.duplicate_of_id is not None,
            "adopted": False,
            "no_op": False,
            "root": root,
        }


def clean_downloaded_item(item, *, converter: Converter | None = None) -> dict:
    if item.duplicate_of_id:
        raise ValueError(f"Duplicate item must use canonical item {item.duplicate_of_id}")
    if item.status != IntakeState.DOWNLOADED.value:
        raise ValueError("Item must be DOWNLOADED before cleaning")
    source = intake_storage.resolve_stored_path(item.original_path)
    if source.is_symlink() or not source.is_file():
        raise intake_storage.IntakeStorageError("Downloaded source is not a regular intake file")
    transition_item(item, IntakeState.CLEANING)
    try:
        converter = converter or MarkItDownAdapter()
        extracted = converter.convert_to_markdown(source)
        cleaned, removed, warnings = clean_extracted_text(extracted)
        cleaned_path = intake_storage.clean_path(
            item.batch.code,
            item.batch.source_language,
            item.order_index,
        )
        intake_storage.atomic_write_text(cleaned_path, cleaned)
        audit = {
            "item_id": item.id,
            "source_filename": item.source_filename,
            "source_sha256": item.source_sha256,
            "original_path": item.original_path,
            "clean_path": intake_storage.relative_storage_path(cleaned_path),
            "removed_lines": removed,
            "warnings": warnings,
            "needs_review": bool(warnings),
            "duplicate_of_item_id": item.duplicate_of_id,
        }
        intake_storage.atomic_write_json(
            intake_storage.audit_path(
                item.batch.code,
                item.batch.source_language,
                item.order_index,
            ),
            audit,
        )
        item.clean_path = intake_storage.relative_storage_path(cleaned_path)
        item.last_error = ""
        item.save(update_fields=["clean_path", "last_error", "updated_at"])
        transition_item(item, IntakeState.CLEAN_READY)
        return {"item": item, "audit": audit}
    except Exception as exc:
        if item.status != IntakeState.FAILED.value:
            transition_item(item, IntakeState.FAILED, error=str(exc))
        raise
