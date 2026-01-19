from __future__ import annotations

import sys
from pathlib import Path

from django.conf import settings

from editorial.models import EditionText

PROJECT_ROOT = Path(settings.BASE_DIR).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from gaiden.chunker import make_chunks_from_text, write_chunks
from gaiden.structure import detect_units

from . import edition_meta


def _parse_book_id(book_code: str) -> int:
    if book_code.startswith("book_"):
        return int(book_code.split("_", 1)[1])
    digits = "".join(ch for ch in book_code if ch.isdigit())
    if not digits:
        raise ValueError("book_code must include digits (ex: book_0001).")
    return int(digits)


def _get_normalized_text(edition) -> str:
    texts = EditionText.objects.filter(edition=edition).first()
    if texts and texts.normalized_text:
        return texts.normalized_text
    if texts and texts.normalized_path:
        path = Path(texts.normalized_path)
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise ValueError("Normalize required: no normalized_text found.")


def _ensure_normalized_file(edition, normalized_text: str) -> Path:
    book_code = edition_meta.book_code(edition)
    language = edition_meta.language_code(edition)
    out_dir = PROJECT_ROOT / "data" / "normalized"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{book_code}_{language}_v2.txt"
    if not out_path.exists():
        out_path.write_text(normalized_text, encoding="utf-8")
    return out_path


def run_split_struct(edition) -> int:
    normalized = _get_normalized_text(edition)
    _ensure_normalized_file(edition, normalized)
    units = detect_units(normalized.splitlines())
    return len(units)


def run_split_01(edition, min_tokens: int = 1500, target_tokens: int = 1800, max_tokens: int = 2200) -> int:
    normalized = _get_normalized_text(edition)
    _ensure_normalized_file(edition, normalized)
    book_id = _parse_book_id(edition_meta.book_code(edition))
    chunks = make_chunks_from_text(normalized, edition_meta.language_code(edition), min_tokens, target_tokens, max_tokens)
    chunks = write_chunks(book_id, "split_01", chunks)
    return len(chunks)
