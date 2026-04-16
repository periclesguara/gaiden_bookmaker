from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

from gaiden.lang import normalize_lang_code


def normalize_book_code(value: str | int | None) -> str:
    raw = str(value or "").strip().lower()
    match = re.search(r"(\d+)", raw)
    if not match:
        raise ValueError(f"Invalid book code: {value!r}")
    return f"book_{int(match.group(1)):04d}"


def normalize_mode(value: str | None, default: str = "default") -> str:
    raw = (value or default or "default").strip().lower()
    raw = re.sub(r"[^a-z0-9_]+", "_", raw).strip("_")
    return raw or default or "default"


def lang_token(value: str | None) -> str:
    return normalize_lang_code(value, default="en")


def canonical_artifact_path(out_dir: str | Path, book_id: str, suffix: str, mode: str) -> Path:
    book = normalize_book_code(book_id)
    lang = lang_token(suffix)
    route = normalize_mode(mode, default="default")
    return Path(out_dir) / f"{book}__{route}__{lang}.txt"


def canonical_meta_path(path: str | Path) -> Path:
    p = Path(path)
    return p.with_suffix(p.suffix + ".meta.json")


def assert_valid_canonical_artifact(path: str | Path) -> None:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if not p.read_text(encoding="utf-8", errors="strict").strip():
        raise ValueError(f"Canonical artifact is empty: {p}")


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def source_input_hash(paths: Iterable[str | Path]) -> str:
    h = hashlib.sha256()
    for path in sorted(Path(p) for p in paths):
        h.update(path.name.encode("utf-8"))
        h.update(b"\0")
        h.update(sha256_file(path).encode("ascii"))
        h.update(b"\0")
    return h.hexdigest()


def write_canonical_meta(
    artifact_path: str | Path,
    *,
    route: str,
    artifact_sha256: str,
    input_source_hash: str,
) -> Path:
    p = canonical_meta_path(artifact_path)
    p.write_text(
        json.dumps(
            {
                "artifact": Path(artifact_path).name,
                "route": normalize_mode(route, default="default"),
                "artifact_sha256": artifact_sha256,
                "input_source_hash": input_source_hash,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return p


def write_active_pointer(out_dir: str | Path, book_id: str, suffix: str, artifact_filename: str) -> Path:
    p = Path(out_dir) / "active_artifact.json"
    p.write_text(
        json.dumps(
            {
                "book_id": normalize_book_code(book_id),
                "lang": lang_token(suffix),
                "artifact_filename": artifact_filename,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return p


def resolve_active_or_latest(out_dir: str | Path, book_id: str | None = None, suffix: str | None = None) -> Path | None:
    base = Path(out_dir)
    active = base / "active_artifact.json"
    if active.exists():
        payload = json.loads(active.read_text(encoding="utf-8"))
        filename = payload.get("artifact_filename")
        if filename and (base / filename).exists():
            return base / filename
    candidates = sorted(base.glob("book_*__*__*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if suffix:
        lang = lang_token(suffix)
        candidates = [p for p in candidates if p.stem.endswith(f"__{lang}")]
    if book_id:
        book = normalize_book_code(book_id)
        candidates = [p for p in candidates if p.name.startswith(f"{book}__")]
    return candidates[0] if candidates else None
