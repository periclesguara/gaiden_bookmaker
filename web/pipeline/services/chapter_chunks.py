from __future__ import annotations

from pathlib import Path
import os
import sys

from django.conf import settings

from editorial.models import EditionText

PROJECT_ROOT = Path(settings.BASE_DIR).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from gaiden.chunk_engine import resolve_and_run

from . import edition_meta, paths, utils


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
    lang_dir = utils.normalize_lang(language_code)
    canonical = paths.data_dir() / "normalized" / book_code / lang_dir / f"{book_code}_{lang_dir}_v2.txt"
    if not canonical.exists():
        raise FileNotFoundError(f"Normalized text not found: {canonical}")
    return canonical


def run_chapter_chunks(edition) -> dict[str, str]:
    language_code = edition_meta.language_code(edition)
    if language_code != "en":
        raise ValueError("chunk stage suporta apenas ingles no momento.")

    book_code = edition_meta.book_code(edition)
    book_id = _parse_book_id(book_code)
    if book_id is None:
        raise ValueError("book_code must be like book_0001.")

    normalized_path = _resolve_normalized_path(edition)

    output_dir = paths.data_dir() / "chunks" / f"book_{book_id:04d}" / "en"
    manifest_path = output_dir / "chunks_manifest.json"

    def _env_int(name: str, default: int) -> int:
        raw_val = os.getenv(name)
        if not raw_val:
            return default
        try:
            return int(raw_val)
        except ValueError:
            return default

    target_tokens = _env_int("GAIDEN_CHUNK_TARGET_TOKENS", 1500)
    max_tokens = _env_int("GAIDEN_CHUNK_MAX_TOKENS", 2000)

    result = resolve_and_run(
        book_code=book_code,
        lang="en",
        normalized_path=normalized_path,
        out_dir=output_dir,
        target_tokens=target_tokens,
        max_tokens=max_tokens,
        dry_run=False,
    )

    return {
        "path": str(output_dir),
        "manifest": str(manifest_path),
        "run_report": str(output_dir / "chunk_run_report.json"),
        "check_ok": str(result.get("checks", {}).get("check_ok")),
    }
