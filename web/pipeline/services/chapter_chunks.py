from __future__ import annotations

from pathlib import Path
import sys

from django.conf import settings

from editorial.models import EditionText

PROJECT_ROOT = Path(settings.BASE_DIR).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from gaiden.chapter_chunks import build_chapter_chunks

from . import edition_meta, paths


def _parse_book_id(book_code: str) -> int | None:
    try:
        if book_code.startswith("book_"):
            return int(book_code.split("_", 1)[1])
    except (ValueError, IndexError):
        return None

    digits = "".join(ch for ch in book_code if ch.isdigit())
    try:
        return int(digits) if digits else None
    except ValueError:
        return None


def _resolve_normalized_path(edition) -> Path:
    book_code = edition_meta.book_code(edition)
    language_code = edition_meta.language_code(edition)
    texts = EditionText.objects.filter(edition=edition).first()
    if texts and texts.normalized_path:
        path = Path(texts.normalized_path)
        if path.exists():
            return path
    if texts and texts.normalized_text:
        fallback_dir = paths.data_dir() / "normalized"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        path = fallback_dir / f"{book_code}_{language_code}_v2.txt"
        path.write_text(texts.normalized_text, encoding="utf-8")
        return path
    book_id = _parse_book_id(book_code)
    if book_id is None:
        raise ValueError("book_code must be like book_0001.")
    fallback = paths.data_dir() / "normalized" / f"{book_code}_{language_code}_v2.txt"
    if not fallback.exists():
        raise FileNotFoundError(f"Normalized text not found: {fallback}")
    return fallback


def run_split_by_chapter(edition) -> dict[str, str]:
    language_code = edition_meta.language_code(edition)
    if language_code != "en":
        raise ValueError("split_by_chapter so suporta ingles no momento.")

    book_code = edition_meta.book_code(edition)
    book_id = _parse_book_id(book_code)
    if book_id is None:
        raise ValueError("book_code must be like book_0001.")

    normalized_path = _resolve_normalized_path(edition)
    raw = normalized_path.read_text(encoding="utf-8")

    output_dir = paths.data_dir() / "chunks" / f"book_{book_id:04d}" / "split_01_by_chapter"
    manifest_path = output_dir / "chunks_by_chapter.json"
    normalized_out = output_dir / "normalized_chapterized.txt"

    result = build_chapter_chunks(raw, output_dir, manifest_path, language=language_code)
    normalized_out.write_text(result["normalized_text"], encoding="utf-8")

    return {
        "path": str(output_dir),
        "manifest": str(manifest_path),
        "normalized": str(normalized_out),
    }
