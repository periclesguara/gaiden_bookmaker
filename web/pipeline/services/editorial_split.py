from __future__ import annotations

from pathlib import Path

from django.conf import settings

from editorial.models import EditionText

from . import edition_meta, chapter_chunks, paths


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


def run_chunks(edition) -> int:
    """Legacy wrapper to generate EN chapter chunks."""
    result = chapter_chunks.run_chapter_chunks(edition)
    book_id = _parse_book_id(edition_meta.book_code(edition))
    out_dir = paths.data_dir() / "chunks" / f"book_{book_id:04d}" / "en"
    return len(list(out_dir.glob("*.txt")))
