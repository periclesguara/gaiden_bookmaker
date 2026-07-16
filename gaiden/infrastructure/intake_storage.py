from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from gaiden.infrastructure import storage


SAFE_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class IntakeStorageError(ValueError):
    pass


def safe_segment(value: str, *, label: str) -> str:
    normalized = (value or "").strip().lower()
    if not SAFE_SEGMENT.fullmatch(normalized):
        raise IntakeStorageError(f"Unsafe {label}: {value!r}")
    return normalized


def intake_root() -> Path:
    return storage.data_dir() / "intake"


def batch_root(batch_code: str, source_language: str) -> Path:
    return intake_root() / safe_segment(batch_code, label="batch code") / safe_segment(
        source_language, label="source language"
    )


def ensure_batch_layout(batch_code: str, source_language: str) -> Path:
    root = batch_root(batch_code, source_language)
    for path in (root / "originals", root / "cleaned", root / "translation", root / "audit"):
        path.mkdir(parents=True, exist_ok=True)
    return root


def item_code(order_index: int) -> str:
    if order_index < 1:
        raise IntakeStorageError("Item order must be positive")
    return f"item_{order_index:04d}"


def original_path(batch_code: str, source_language: str, order_index: int, suffix: str) -> Path:
    ext = suffix.lower()
    if not re.fullmatch(r"\.(epub|txt|html|htm)", ext):
        raise IntakeStorageError(f"Unsupported source suffix: {suffix}")
    return batch_root(batch_code, source_language) / "originals" / f"{item_code(order_index)}_original{ext}"


def clean_path(batch_code: str, source_language: str, order_index: int) -> Path:
    return batch_root(batch_code, source_language) / "cleaned" / f"{item_code(order_index)}_clean.txt"


def translation_dir(batch_code: str, source_language: str, order_index: int, target_language: str) -> Path:
    return (
        batch_root(batch_code, source_language)
        / "translation"
        / item_code(order_index)
        / safe_segment(target_language, label="target language")
    )


def translation_input_path(
    batch_code: str, source_language: str, order_index: int, target_language: str
) -> Path:
    return translation_dir(batch_code, source_language, order_index, target_language) / "input" / (
        f"{item_code(order_index)}_clean.txt"
    )


def translation_return_path(
    batch_code: str, source_language: str, order_index: int, target_language: str
) -> Path:
    language = safe_segment(target_language, label="target language")
    return translation_dir(batch_code, source_language, order_index, language) / "return" / (
        f"{item_code(order_index)}_clean_translate_{language}.txt"
    )


def manifest_path(batch_code: str, source_language: str) -> Path:
    return batch_root(batch_code, source_language) / "manifest.json"


def audit_path(batch_code: str, source_language: str, order_index: int) -> Path:
    return batch_root(batch_code, source_language) / "audit" / f"{item_code(order_index)}_cleaning.json"


def drive_audit_path(batch_code: str, source_language: str) -> Path:
    return batch_root(batch_code, source_language) / "audit" / "drive_sync_report.json"


def translation_manifest_path(
    batch_code: str, source_language: str, order_index: int, target_language: str
) -> Path:
    return translation_dir(batch_code, source_language, order_index, target_language) / "manifest.json"


def relative_storage_path(path: Path) -> str:
    root = storage.data_dir().resolve()
    candidate = path.resolve()
    try:
        return str(candidate.relative_to(root))
    except ValueError as exc:
        raise IntakeStorageError(f"Path escapes canonical storage: {path}") from exc


def resolve_stored_path(path_value: str) -> Path:
    candidate = Path(path_value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise IntakeStorageError(f"Unsafe stored path: {path_value!r}")
    resolved = (storage.data_dir() / candidate).resolve()
    relative_storage_path(resolved)
    return resolved


def atomic_write_bytes(path: Path, payload: bytes, *, overwrite: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite intake artifact: {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite intake artifact: {path}")
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return path


def atomic_write_text(path: Path, text: str, *, overwrite: bool = False) -> Path:
    return atomic_write_bytes(path, text.encode("utf-8"), overwrite=overwrite)


def atomic_write_json(path: Path, payload: dict, *, overwrite: bool = False) -> Path:
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return atomic_write_text(path, content, overwrite=overwrite)
