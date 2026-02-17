from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gaiden.chunk_contract import SCHEMA_VERSION


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_manifest_v2(
    *,
    book_code: str,
    lang: str,
    normalized_path: Path,
    normalized_sha256: str,
    chunker_version: str,
    created_at: str,
    config: dict[str, Any],
    headings_detected_count: int,
    single_chapter_mode: bool,
    chapters: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "book_code": book_code,
        "lang": lang,
        "normalized_path": str(normalized_path),
        "normalized_sha256": normalized_sha256,
        "chunker_version": chunker_version,
        "created_at": created_at,
        "config": config,
        "headings_detected_count": headings_detected_count,
        "single_chapter_mode": single_chapter_mode,
        "chapters": chapters,
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
