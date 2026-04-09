from __future__ import annotations

from pathlib import Path

from . import ingest, normalization


def extract_and_normalize(path: Path) -> tuple[str, str]:
    ext = path.suffix.lstrip(".")
    raw_text = ingest.extract_text_from_file(path, ext)
    if not raw_text:
        raise ValueError(f"Could not extract text from {path}")
    normalized_text = normalization.normalize_text_v2(raw_text)
    return raw_text, normalized_text
