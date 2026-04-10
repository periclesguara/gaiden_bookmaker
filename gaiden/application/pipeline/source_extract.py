from __future__ import annotations

import logging
import shutil
from pathlib import Path

from gaiden.infrastructure.source_extractors import HtmlExtractor, TxtExtractor, EpubExtractor
from gaiden.infrastructure.source_extractors.base import canonical_paths

logger = logging.getLogger(__name__)

SOURCE_EXTRACT_SCHEMA = "source_extract_v1"
SOURCE_STATUS_UPLOADED = "SOURCE_UPLOADED"
SOURCE_STATUS_EXTRACTED = "SOURCE_EXTRACTED"

EXTENSION_MAP = {
    ".txt": TxtExtractor,
    ".html": HtmlExtractor,
    ".htm": HtmlExtractor,
    ".epub": EpubExtractor,
}


class UnsupportedSourceFormatError(ValueError):
    pass


def supported_extensions() -> set[str]:
    return set(EXTENSION_MAP)


def detect_source_extension(path: str | Path) -> str:
    ext = Path(path).suffix.lower().strip()
    if ext not in EXTENSION_MAP:
        accepted = ", ".join(sorted(EXTENSION_MAP))
        raise UnsupportedSourceFormatError(f"Unsupported source format: {ext or '(none)'}. Accepted: {accepted}.")
    return ext


def run_source_extract(book_code: str, lang: str, uploaded_file_path: str | Path) -> dict:
    source_path = Path(uploaded_file_path).expanduser()
    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    ext = detect_source_extension(source_path)
    paths = canonical_paths(book_code, lang, ext)
    paths.raw_dir.mkdir(parents=True, exist_ok=True)
    paths.images_dir.mkdir(parents=True, exist_ok=True)

    if source_path.resolve() != paths.original_file.resolve():
        shutil.copy2(source_path, paths.original_file)

    logger.info("source_extract_uploaded book=%s lang=%s path=%s", book_code, lang, paths.original_file)
    extractor_cls = EXTENSION_MAP[ext]
    result = extractor_cls().extract(paths.original_file, book_code=book_code, lang=lang)
    logger.info(
        "source_extract_completed book=%s lang=%s format=%s txt=%s html=%s",
        book_code,
        lang,
        result.get("input_format"),
        result.get("canonical_txt"),
        result.get("canonical_html"),
    )
    return result
