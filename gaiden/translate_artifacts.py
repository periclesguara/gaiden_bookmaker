from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from gaiden.lang import normalize_lang_code

BOOK_CODE_RE = re.compile(r"^book_(\d{4})$")
BOOK_ID_RE = re.compile(r"^(\d{4})$")

MODE_ALIASES = {
    "automatic": "automatic",
    "auto": "automatic",
    "default": "default",
}
VALID_MODES = {"automatic", "default"}
DEFAULT_CANONICAL_MIN_BYTES = 64


def normalize_mode(mode: str | None, *, default: str = "automatic") -> str:
    raw = (mode or default).strip().lower()
    normalized = MODE_ALIASES.get(raw, "")
    if normalized in VALID_MODES:
        return normalized
    return default


def normalize_book_code(book: str | int) -> str:
    if isinstance(book, int):
        return f"book_{book:04d}"
    raw = str(book).strip()
    if BOOK_CODE_RE.match(raw):
        return raw
    if BOOK_ID_RE.match(raw):
        return f"book_{int(raw):04d}"
    if raw.startswith("book_"):
        digits = "".join(ch for ch in raw if ch.isdigit())
        if digits:
            return f"book_{int(digits):04d}"
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits:
        return f"book_{int(digits):04d}"
    raise ValueError(f"Invalid book identifier: {book}")


def book_id_token(book: str | int) -> str:
    code = normalize_book_code(book)
    m = BOOK_CODE_RE.match(code)
    if not m:
        raise ValueError(f"Invalid normalized book code: {code}")
    return m.group(1)


def lang_token(lang: str | None) -> str:
    canonical = normalize_lang_code(lang or "en", default="en")
    if canonical == "en_modern":
        return "en"
    return canonical


def canonical_artifact_filename(book: str | int, lang: str, mode: str) -> str:
    return (
        f"book_{book_id_token(book)}_{lang_token(lang)}_"
        f"{normalize_mode(mode)}_merge_clean.txt"
    )


def active_pointer_filename(book: str | int, lang: str) -> str:
    return f"book_{book_id_token(book)}_{lang_token(lang)}_ACTIVE_MERGE.txt"


def canonical_artifact_path(out_dir: Path, book: str | int, lang: str, mode: str) -> Path:
    return Path(out_dir) / canonical_artifact_filename(book, lang, mode)


def active_pointer_path(out_dir: Path, book: str | int, lang: str) -> Path:
    return Path(out_dir) / active_pointer_filename(book, lang)


def canonical_meta_path(path: Path) -> Path:
    return path.with_suffix(".meta.json")


def canonical_min_bytes(default: int = DEFAULT_CANONICAL_MIN_BYTES) -> int:
    raw = str(os.getenv("GAIDEN_CANONICAL_MIN_BYTES", str(default))).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < 1:
        return default
    return value


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def source_input_hash(files: list[Path]) -> str:
    h = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.name):
        h.update(path.name.encode("utf-8", errors="strict"))
        h.update(b"\0")
        h.update(sha256_file(path).encode("ascii", errors="strict"))
        h.update(b"\n")
    return h.hexdigest()


def write_canonical_meta(
    canonical_path: Path,
    *,
    route: str,
    artifact_sha256: str,
    input_source_hash: str | None = None,
    timestamp: str | None = None,
) -> Path:
    meta = {
        "route": route,
        "hash": artifact_sha256,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "input_source_hash": input_source_hash,
    }
    meta_path = canonical_meta_path(canonical_path)
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return meta_path


def _valid_filename_for_book_lang(name: str, book: str | int, lang: str) -> bool:
    expected_prefix = f"book_{book_id_token(book)}_{lang_token(lang)}_"
    return (
        name.startswith(expected_prefix)
        and name.endswith("_merge_clean.txt")
        and (
            name == canonical_artifact_filename(book, lang, "automatic")
            or name == canonical_artifact_filename(book, lang, "default")
        )
    )


def validate_canonical_artifact(
    path: Path,
    *,
    min_bytes: int | None = None,
) -> list[str]:
    errors: list[str] = []
    threshold = min_bytes if min_bytes is not None else canonical_min_bytes()

    if not path.exists():
        return [f"missing_file:{path}"]
    if not path.is_file():
        return [f"not_a_file:{path}"]

    size = path.stat().st_size
    if size <= threshold:
        errors.append(f"size_below_threshold:{size}<={threshold}")

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"read_error:{type(exc).__name__}"]

    if not content.strip():
        errors.append("empty_after_strip")
    if not content.endswith("\n"):
        errors.append("missing_trailing_newline")

    lowered = content.lower()
    if "[error]" in lowered:
        errors.append("placeholder:[ERROR]")
    if "content filtered" in lowered:
        errors.append("placeholder:content filtered")
    if re.search(r"(?m)^\s*None\s*$", content):
        errors.append("placeholder:None")
    if re.search(r"(?m)\bTODO\b", content):
        errors.append("placeholder:TODO")

    return errors


def assert_valid_canonical_artifact(path: Path, *, min_bytes: int | None = None) -> None:
    errors = validate_canonical_artifact(path, min_bytes=min_bytes)
    if errors:
        raise RuntimeError(
            "CANONICAL_ARTIFACT_INVALID: " + ";".join(errors)
        )


def write_active_pointer(out_dir: Path, book: str | int, lang: str, artifact_name: str) -> Path:
    path = active_pointer_path(out_dir, book, lang)
    path.parent.mkdir(parents=True, exist_ok=True)
    target_name = artifact_name.strip()
    if not _valid_filename_for_book_lang(target_name, book, lang):
        raise ValueError(
            f"Invalid canonical artifact for ACTIVE pointer: {target_name}"
        )

    tmp_path = Path(str(path) + ".tmp")
    data = (target_name + "\n").encode("utf-8")
    fd = None
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        os.write(fd, data)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(tmp_path, path)

        # Best effort: ensure pointer rename reaches disk.
        dir_fd = None
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            if dir_fd is not None:
                os.close(dir_fd)
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return path


def read_active_pointer(out_dir: Path, book: str | int, lang: str) -> Path | None:
    pointer = active_pointer_path(out_dir, book, lang)
    if not pointer.exists():
        return None
    try:
        target_name = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not target_name:
        return None
    if not _valid_filename_for_book_lang(target_name, book, lang):
        return None
    target_path = Path(out_dir) / target_name
    if not target_path.exists():
        return None
    return target_path


def list_canonical_artifacts(out_dir: Path, book: str | int, lang: str) -> list[Path]:
    prefix = f"book_{book_id_token(book)}_{lang_token(lang)}_"
    candidates = sorted(
        [
            p
            for p in Path(out_dir).glob(f"{prefix}*_merge_clean.txt")
            if p.is_file() and _valid_filename_for_book_lang(p.name, book, lang)
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates


def resolve_active_or_latest(out_dir: Path, book: str | int, lang: str) -> Path | None:
    active = read_active_pointer(out_dir, book, lang)
    if active:
        return active
    candidates = list_canonical_artifacts(out_dir, book, lang)
    if candidates:
        return candidates[0]
    return None
