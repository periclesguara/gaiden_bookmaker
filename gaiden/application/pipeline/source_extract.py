from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path

from gaiden.infrastructure.source_extractors import HtmlExtractor, TxtExtractor, EpubExtractor
from gaiden.infrastructure.source_extractors.base import canonical_paths
from gaiden.infrastructure.source_extractors.epub_extractor import COSMETIC_FILENAMES
from gaiden.infrastructure.source_extractors.epub_reader import EpubReader
from gaiden.infrastructure.source_extractors.html_extractor import html_to_text

logger = logging.getLogger(__name__)

SOURCE_EXTRACT_SCHEMA = "source_extract_v1"
SOURCE_STATUS_UPLOADED = "SOURCE_UPLOADED"
SOURCE_STATUS_EXTRACTED = "SOURCE_EXTRACTED"
READING_PREVIEW_MAX_CHARACTERS = 24_000

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


def _limited_text_preview(path: Path, max_characters: int) -> tuple[str, bool]:
    with path.open("r", encoding="utf-8", errors="replace") as source:
        text = source.read(max_characters + 1)
    return text[:max_characters].strip(), len(text) > max_characters


def _limited_html_preview(path: Path, max_characters: int) -> tuple[str, bool]:
    raw_limit = max(64_000, max_characters * 8)
    with path.open("r", encoding="utf-8", errors="replace") as source:
        raw_html = source.read(raw_limit + 1)
    text = html_to_text(raw_html)
    return text[:max_characters].strip(), len(raw_html) > raw_limit or len(text) > max_characters


def _is_epub_html_item(item: dict) -> bool:
    media_type = item.get("media_type", "")
    item_path = item.get("path", "").lower()
    return media_type in {"application/xhtml+xml", "text/html"} or item_path.endswith((".xhtml", ".html", ".htm"))


def _epub_reading_preview(path: Path, max_characters: int) -> tuple[str, str, bool]:
    try:
        package = EpubReader(path).read()
        parts: list[str] = []
        truncated = False
        with zipfile.ZipFile(path, "r") as archive:
            for index, item in enumerate(package.spine):
                item_path = item.get("path", "")
                if not _is_epub_html_item(item) or Path(item_path).name.lower() in COSMETIC_FILENAMES:
                    continue
                remaining = max_characters - len("\n\n".join(parts))
                if remaining <= 0:
                    truncated = True
                    break
                raw_limit = max(64_000, remaining * 8)
                try:
                    with archive.open(item_path) as source:
                        raw_bytes = source.read(raw_limit + 1)
                except KeyError:
                    continue
                chapter = html_to_text(raw_bytes[:raw_limit].decode("utf-8", errors="replace"))
                if chapter:
                    parts.append(chapter)
                combined = "\n\n".join(parts)
                if len(raw_bytes) > raw_limit or len(combined) > max_characters:
                    truncated = True
                    break
                if len(combined) == max_characters and index < len(package.spine) - 1:
                    truncated = True
                    break
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError(f"EPUB inválido: {exc}") from exc

    text = "\n\n".join(parts).strip()
    return str(package.metadata.get("title") or path.stem), text[:max_characters], truncated or len(text) > max_characters


def build_reading_preview(
    source_file_path: str | Path,
    *,
    max_characters: int = READING_PREVIEW_MAX_CHARACTERS,
) -> dict[str, object]:
    """Build a bounded, read-only text preview without creating canonical artifacts."""
    if max_characters < 1:
        raise ValueError("O limite da prévia deve ser maior que zero.")
    source_path = Path(source_file_path).expanduser()
    if not source_path.is_file():
        raise FileNotFoundError(f"Source file not found: {source_path}")
    extension = detect_source_extension(source_path)
    title = source_path.stem
    if extension == ".txt":
        text, truncated = _limited_text_preview(source_path, max_characters)
    elif extension in {".html", ".htm"}:
        text, truncated = _limited_html_preview(source_path, max_characters)
    else:
        title, text, truncated = _epub_reading_preview(source_path, max_characters)
    if not text:
        raise ValueError("O arquivo importado não possui texto legível para a prévia.")
    return {
        "title": title,
        "input_format": extension.lstrip("."),
        "text": text,
        "truncated": truncated,
        "visible_characters": len(text),
    }


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
