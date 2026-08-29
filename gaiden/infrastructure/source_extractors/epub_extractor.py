from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path

from .base import canonical_paths, ensure_canonical_dirs, normalize_result, write_source_meta
from .epub_reader import EpubReader
from .html_extractor import html_to_text

COSMETIC_FILENAMES = {
    "titlepage.xhtml",
    "titlepage.html",
    "halftitlepage.xhtml",
    "halftitlepage.html",
    "imprint.xhtml",
    "imprint.html",
    "uncopyright.xhtml",
    "uncopyright.html",
    "colophon.xhtml",
    "colophon.html",
}

_CHAPTER_SECTION_RE = re.compile(
    r"<section\b[^>]*\bid=[\"']chapter-(\d+)[\"'][^>]*>",
    re.IGNORECASE,
)
_SEMANTIC_CHAPTER_SECTION_RE = re.compile(
    r"<section\b[^>]*(?:\bepub:type|\btype|\brole)=[\"'][^\"']*\bchapter\b[^\"']*[\"'][^>]*>",
    re.IGNORECASE,
)
_ORDINAL_HEADING_RE = re.compile(
    r"(?is)(?P<open><h[1-6]\b[^>]*>)\s*"
    r"(?P<ordinal>[IVXLCDM]+|\d+)\.?\s*(?P<close></h[1-6]>)"
)


def _safe_image_name(path: str, used: set[str]) -> str:
    name = Path(path).name or "image"
    stem = Path(name).stem or "image"
    suffix = Path(name).suffix
    candidate = name
    index = 2
    while candidate in used:
        candidate = f"{stem}_{index}{suffix}"
        index += 1
    used.add(candidate)
    return candidate


def _body_inner(raw_html: str) -> str:
    match = re.search(r"(?is)<body\b[^>]*>(.*?)</body>", raw_html)
    return match.group(1).strip() if match else raw_html.strip()


def _canonicalize_structural_chapter_heading(raw_html: str) -> str:
    """Preserve EPUB chapter semantics in the text artifact.

    Standard Ebooks commonly labels a semantic ``chapter-N`` section with an
    ordinal-only heading (``I``, ``II``, ...).  Plain-text conversion loses
    the semantic wrapper and makes those headings indistinguishable from a
    standalone pronoun in the prose.  Turn only that structural heading into
    the unambiguous pipeline contract ``CHAPTER N`` before text extraction.
    """
    section = _CHAPTER_SECTION_RE.search(raw_html)
    semantic_section = _SEMANTIC_CHAPTER_SECTION_RE.search(raw_html)
    if not section and not semantic_section:
        return raw_html
    chapter_number = section.group(1) if section else ""

    def replace_heading(match: re.Match[str]) -> str:
        number = chapter_number or match.group("ordinal")
        return f"{match.group('open')}CHAPTER {number}{match.group('close')}"

    return _ORDINAL_HEADING_RE.sub(
        replace_heading,
        raw_html,
        count=1,
    )


def _is_html_item(item: dict) -> bool:
    media_type = item.get("media_type", "")
    return media_type in {"application/xhtml+xml", "text/html"} or item.get("path", "").lower().endswith((".xhtml", ".html", ".htm"))


class EpubExtractor:
    input_format = "epub"

    def extract(self, original_file: Path, *, book_code: str, lang: str) -> dict:
        paths = canonical_paths(book_code, lang, ".epub")
        ensure_canonical_dirs(paths)
        reader = EpubReader(original_file)
        package = reader.read()
        warnings: list[str] = []

        html_parts: list[str] = []
        with zipfile.ZipFile(original_file, "r") as zf:
            for item in package.spine:
                item_path = item.get("path", "")
                if not _is_html_item(item):
                    continue
                if Path(item_path).name.lower() in COSMETIC_FILENAMES:
                    warnings.append(f"Skipped cosmetic spine item: {item_path}")
                    continue
                try:
                    raw = zf.read(item_path).decode("utf-8", errors="replace")
                except KeyError:
                    warnings.append(f"Missing spine item: {item_path}")
                    continue
                raw = _canonicalize_structural_chapter_heading(raw)
                html_parts.append(f'<section data-source="{item_path}">\n{_body_inner(raw)}\n</section>')

            images_count = self._extract_images(zf, package.manifest, paths.images_dir)

        if not html_parts:
            raise ValueError("EPUB extract produced no readable spine HTML.")

        title = package.metadata.get("title") or ""
        canonical_html = (
            '<!doctype html>\n'
            '<html><head><meta charset="utf-8">'
            f"<title>{title}</title>"
            "</head><body>\n"
            + "\n".join(html_parts)
            + "\n</body></html>\n"
        )
        paths.canonical_html.write_text(canonical_html, encoding="utf-8")
        paths.canonical_txt.write_text(html_to_text(canonical_html) + "\n", encoding="utf-8")

        details = {
            "title": title,
            "creators": package.metadata.get("creators", []),
            "languages": package.metadata.get("languages", []),
            "publisher": package.metadata.get("publisher", ""),
            "rights": package.metadata.get("rights", ""),
            "spine_count": len(package.spine),
            "toc_count": len(package.toc),
            "images_count": images_count,
        }

        result = normalize_result(input_format=self.input_format, paths=paths, warnings=warnings, details=details)
        write_source_meta(result, paths)
        return result

    def _extract_images(self, zf: zipfile.ZipFile, manifest: dict[str, dict], images_dir: Path) -> int:
        images_dir.mkdir(parents=True, exist_ok=True)
        used: set[str] = set()
        count = 0
        for item in manifest.values():
            media_type = item.get("media_type", "")
            if not media_type.startswith("image/"):
                continue
            item_path = posixpath.normpath(item.get("path", ""))
            if not item_path:
                continue
            try:
                data = zf.read(item_path)
            except KeyError:
                continue
            image_name = _safe_image_name(item_path, used)
            (images_dir / image_name).write_bytes(data)
            count += 1
        return count
