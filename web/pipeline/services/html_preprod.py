from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from django.conf import settings

from . import utils

try:
    from bs4 import BeautifulSoup, Comment, NavigableString
except ImportError:  # pragma: no cover - fallback path covered when bs4 is absent
    BeautifulSoup = None
    Comment = None
    NavigableString = None


HTML_EXTENSIONS = {".html", ".htm"}
START_RE = re.compile(r"\*\*\*\s*START OF", re.IGNORECASE)
END_RE = re.compile(r"\*\*\*\s*END OF", re.IGNORECASE)
CHAPTER_RE = re.compile(
    r"^\s*(chapter|part|cap[ií]tulo|teil)\b[\s\.:_-]*([ivxlcdm]+|\d+)?",
    re.IGNORECASE,
)
PURE_CHAPTER_NUMBER_RE = re.compile(r"^\s*([ivxlcdm]+|\d+)\s*$", re.IGNORECASE)
TOC_TITLE_RE = re.compile(r"^(contents|table of contents)\s*$", re.IGNORECASE)


def artifact_paths(book_code: str, language: str) -> dict[str, Path]:
    language = utils.normalize_lang(language)
    root = Path(settings.BASE_DIR).parent
    return {
        "raw_html": root / "data" / "raw" / book_code / f"{book_code}_{language}_raw.html",
        "raw_htm": root / "data" / "raw" / book_code / f"{book_code}_{language}_raw.htm",
        "preprod_clean_html": root / "data" / "preprod" / book_code / f"{book_code}_{language}_clean.html",
        "preprod_report_json": root / "data" / "preprod" / book_code / f"{book_code}_{language}_report.json",
        "md_source": root / "data" / "md" / book_code / f"{book_code}_{language}_source.md",
        "md_normalized": root / "data" / "md" / book_code / f"{book_code}_{language}_normalized.md",
        "md_canonical": root / "data" / "md" / book_code / f"{book_code}_{language}_canonical.md",
    }


def run_html_preprod(edition) -> tuple[Path, Path, dict[str, Any]]:
    book_code = edition.work.code
    language = utils.normalize_lang(edition.language.code)
    paths = artifact_paths(book_code, language)
    raw_path = _resolve_raw_html_path(edition, book_code, language, paths)

    raw_html = raw_path.read_text(encoding="utf-8", errors="ignore")
    warnings: list[str] = []
    errors: list[str] = []

    trimmed_html, gutenberg_trim_applied = _trim_gutenberg_block(raw_html)
    clean_html, stats = _sanitize_and_structure(trimmed_html, warnings)

    clean_path = paths["preprod_clean_html"]
    clean_path.parent.mkdir(parents=True, exist_ok=True)
    clean_path.write_text(clean_html, encoding="utf-8")

    headings_found = int(stats.get("headings_found", 0))
    chapters_detected = int(stats.get("chapters_detected", 0))
    ok_to_convert = not errors and (headings_found > 0 or chapters_detected > 0)
    if not ok_to_convert:
        warnings.append("Gate bloqueado: nenhum heading/capitulo detectado para conversao segura.")

    report = {
        "edition_id": edition.id,
        "book_code": book_code,
        "language": language,
        "source_format": "html",
        "raw_path": str(raw_path),
        "clean_html_path": str(clean_path),
        "gutenberg_trim_applied": gutenberg_trim_applied,
        "headings_found": headings_found,
        "headings_promoted": int(stats.get("headings_promoted", 0)),
        "chapters_detected": chapters_detected,
        "toc_removed": bool(stats.get("toc_removed", False)),
        "warnings": warnings,
        "errors": errors,
        "ok_to_convert": ok_to_convert,
    }

    report_path = paths["preprod_report_json"]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean_path, report_path, report


def load_preprod_report(edition) -> tuple[Path, dict[str, Any]]:
    book_code = edition.work.code
    language = utils.normalize_lang(edition.language.code)
    report_path = artifact_paths(book_code, language)["preprod_report_json"]
    if not report_path.exists():
        raise FileNotFoundError(f"Report de preprod nao encontrado: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return report_path, report


def run_html_to_md(edition) -> tuple[Path, str]:
    book_code = edition.work.code
    language = utils.normalize_lang(edition.language.code)
    paths = artifact_paths(book_code, language)
    clean_path = paths["preprod_clean_html"]
    if not clean_path.exists():
        raise FileNotFoundError(f"Clean HTML nao encontrado: {clean_path}")

    md_path = paths["md_source"]
    md_path.parent.mkdir(parents=True, exist_ok=True)

    engine = _convert_html_to_md(clean_path, md_path)
    return md_path, engine


def _resolve_raw_html_path(edition, book_code: str, language: str, paths: dict[str, Path]) -> Path:
    root = Path(settings.BASE_DIR).parent
    raw_value = (edition.raw_source_path or "").strip()
    if raw_value:
        candidate = Path(raw_value)
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.exists() and candidate.suffix.lower() in HTML_EXTENSIONS:
            return candidate

    for key in ("raw_html", "raw_htm"):
        if paths[key].exists():
            return paths[key]

    raw_dir = root / "data" / "raw" / book_code
    if raw_dir.exists():
        for candidate in sorted(raw_dir.glob(f"{book_code}_{language}_raw.*")):
            if candidate.suffix.lower() in HTML_EXTENSIONS:
                return candidate

    raise FileNotFoundError("RAW HTML nao encontrado. Faca upload do arquivo .html/.htm no cadastro.")


def _trim_gutenberg_block(html: str) -> tuple[str, bool]:
    start_match = START_RE.search(html)
    end_match = END_RE.search(html)
    if not start_match or not end_match or end_match.start() <= start_match.end():
        return html, False
    return html[start_match.end():end_match.start()], True


def _sanitize_and_structure(html: str, warnings: list[str]) -> tuple[str, dict[str, Any]]:
    if BeautifulSoup is None:
        return _sanitize_with_regex_fallback(html, warnings)

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style"]):
        tag.decompose()

    if Comment is not None:
        for node in soup.find_all(string=lambda value: isinstance(value, Comment)):
            node.extract()

    _normalize_whitespace_nodes(soup)

    headings_before = soup.find_all(["h1", "h2", "h3", "h4"])
    headings_promoted = 0
    if not headings_before:
        headings_promoted = _promote_chapter_paragraphs(soup)
        if headings_promoted == 0:
            warnings.append("Nenhum heading encontrado ou promovido no HTML.")

    toc_removed = _remove_toc_block(soup)
    _normalize_heading_nodes(soup)

    headings_after = soup.find_all(["h1", "h2", "h3", "h4"])
    chapters_detected = sum(1 for tag in headings_after if _looks_like_chapter_heading(tag.get_text(" ", strip=True)))

    clean_html = str(soup)
    clean_html = re.sub(r"[ \t]+\n", "\n", clean_html)
    clean_html = re.sub(r"\n{3,}", "\n\n", clean_html)
    return clean_html, {
        "headings_found": len(headings_after),
        "headings_promoted": headings_promoted,
        "chapters_detected": chapters_detected,
        "toc_removed": toc_removed,
    }


def _sanitize_with_regex_fallback(html: str, warnings: list[str]) -> tuple[str, dict[str, Any]]:
    cleaned = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", html)
    cleaned = re.sub(r"(?is)<style\b[^>]*>.*?</style>", "", cleaned)
    cleaned = re.sub(r"(?s)<!--.*?-->", "", cleaned)

    headings_promoted = 0
    toc_removed = False

    headings_before = len(re.findall(r"(?is)<h[1-4]\b", cleaned))
    if headings_before == 0:
        def _promote(match):
            nonlocal headings_promoted
            text = _normalize_heading_text(_strip_tags(match.group(1)))
            if _looks_like_chapter_heading(text):
                headings_promoted += 1
                return f"<h2>{text}</h2>"
            return match.group(0)

        cleaned = re.sub(
            r"(?is)<p>\s*<(?:b|strong)>(.*?)</(?:b|strong)>\s*</p>",
            _promote,
            cleaned,
        )

    def _normalize_heading_match(match):
        tag = match.group(1)
        text = _normalize_heading_text(_strip_tags(match.group(2)))
        return f"<{tag}>{text}</{tag}>"

    cleaned = re.sub(r"(?is)<(h[1-4])[^>]*>(.*?)</\1>", _normalize_heading_match, cleaned)

    toc_pattern = re.compile(
        r"(?is)<h[1-4][^>]*>\s*(contents|table of contents)\s*</h[1-4]>\s*(<ul.*?</ul>|<ol.*?</ol>)"
    )
    if toc_pattern.search(cleaned):
        cleaned = toc_pattern.sub("", cleaned)
        toc_removed = True

    headings_found = len(re.findall(r"(?is)<h[1-4]\b", cleaned))
    chapters_detected = len(
        re.findall(r"(?is)<h[1-4][^>]*>\s*(chapter|part|cap[ií]tulo|teil)\b", cleaned)
    )
    if headings_found == 0 and headings_promoted == 0:
        warnings.append("Parser fallback sem bs4: nenhum heading encontrado/promovido.")

    return cleaned, {
        "headings_found": headings_found,
        "headings_promoted": headings_promoted,
        "chapters_detected": chapters_detected,
        "toc_removed": toc_removed,
    }


def _normalize_whitespace_nodes(soup: BeautifulSoup) -> None:
    for node in list(soup.find_all(string=True)):
        parent = getattr(node, "parent", None)
        if parent and parent.name in {"script", "style", "pre", "code"}:
            continue
        text = str(node).replace("\xa0", " ")
        text = re.sub(r"\s+", " ", text)
        if text != str(node):
            node.replace_with(text)


def _promote_chapter_paragraphs(soup: BeautifulSoup) -> int:
    promoted = 0
    for p in list(soup.find_all("p")):
        if not p:
            continue
        bold = p.find(["b", "strong"])
        if not bold:
            continue
        bold_text = _collapse_spaces(bold.get_text(" ", strip=True))
        full_text = _collapse_spaces(p.get_text(" ", strip=True))
        if full_text != bold_text:
            continue
        if not _looks_like_chapter_heading(full_text):
            continue
        h2 = soup.new_tag("h2")
        h2.string = _normalize_heading_text(full_text)
        p.replace_with(h2)
        promoted += 1
    return promoted


def _remove_toc_block(soup: BeautifulSoup) -> bool:
    for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
        heading_text = _collapse_spaces(heading.get_text(" ", strip=True))
        if not TOC_TITLE_RE.match(heading_text):
            continue
        sibling = heading.find_next_sibling()
        if sibling is None:
            continue
        if sibling.name in {"ul", "ol"}:
            heading.decompose()
            sibling.decompose()
            return True
        links = sibling.find_all("a")
        if len(links) >= 8:
            heading.decompose()
            sibling.decompose()
            return True
    return False


def _normalize_heading_nodes(soup: BeautifulSoup) -> None:
    for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
        text = _normalize_heading_text(heading.get_text(" ", strip=True))
        heading.clear()
        heading.append(text)


def _looks_like_chapter_heading(text: str) -> bool:
    clean = _collapse_spaces(text)
    return bool(CHAPTER_RE.search(clean) or PURE_CHAPTER_NUMBER_RE.match(clean))


def _normalize_heading_text(text: str) -> str:
    clean = _collapse_spaces(text)
    chapter_number = _chapter_number_from_heading(clean)
    if chapter_number is not None:
        return f"CHAPTER {chapter_number}"

    match = CHAPTER_RE.search(clean)
    if not match:
        return clean

    numeral = match.group(2)
    if numeral and numeral.isalpha():
        converted = _roman_to_int(numeral.upper())
        if converted is not None:
            start, end = match.span(2)
            clean = f"{clean[:start]}{converted}{clean[end:]}"
    return clean


def _collapse_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _chapter_number_from_heading(text: str) -> int | None:
    match = PURE_CHAPTER_NUMBER_RE.match(_collapse_spaces(text))
    if not match:
        return None
    raw_value = match.group(1)
    if raw_value.isdigit():
        try:
            return int(raw_value)
        except ValueError:
            return None
    return _roman_to_int(raw_value.upper())


def _roman_to_int(value: str) -> int | None:
    roman_map = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    previous = 0
    for char in reversed(value):
        current = roman_map.get(char)
        if current is None:
            return None
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total if total > 0 else None


def _strip_tags(html: str) -> str:
    return re.sub(r"(?is)<[^>]+>", "", html or "")


def _convert_html_to_md(clean_html_path: Path, md_path: Path) -> str:
    if shutil.which("pandoc"):
        result = subprocess.run(
            [
                "pandoc",
                "-f",
                "html",
                "-t",
                "markdown",
                "-o",
                str(md_path),
                str(clean_html_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return "pandoc"

    html = clean_html_path.read_text(encoding="utf-8", errors="ignore")
    try:
        from markdownify import markdownify as md_convert

        markdown_text = md_convert(html, heading_style="ATX")
        md_path.write_text(markdown_text.strip() + "\n", encoding="utf-8")
        return "markdownify"
    except Exception:
        markdown_text = _html_to_markdown_fallback(html)
        md_path.write_text(markdown_text.strip() + "\n", encoding="utf-8")
        return "fallback"


def _html_to_markdown_fallback(html: str) -> str:
    if BeautifulSoup is None:
        text = _collapse_spaces(_strip_tags(html))
        return text + "\n"

    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup
    lines: list[str] = []

    for node in body.find_all(recursive=False):
        name = getattr(node, "name", "")
        if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(name[1])
            heading_text = _collapse_spaces(_inline_markdown(node))
            if heading_text:
                lines.append(f"{'#' * level} {heading_text}")
        elif name == "p":
            paragraph = _collapse_spaces(_inline_markdown(node))
            if paragraph:
                lines.append(paragraph)
        elif name in {"ul", "ol"}:
            ordered = name == "ol"
            for idx, li in enumerate(node.find_all("li", recursive=False), start=1):
                content = _collapse_spaces(_inline_markdown(li))
                if not content:
                    continue
                prefix = f"{idx}. " if ordered else "- "
                lines.append(prefix + content)
        else:
            text = _collapse_spaces(_inline_markdown(node))
            if text:
                lines.append(text)

    cleaned_lines: list[str] = []
    for line in lines:
        if line and (not cleaned_lines or cleaned_lines[-1] != line):
            cleaned_lines.append(line)

    return "\n\n".join(cleaned_lines) + "\n"


def _inline_markdown(node) -> str:
    if NavigableString is not None and isinstance(node, NavigableString):
        return str(node)

    name = getattr(node, "name", "")
    parts = "".join(_inline_markdown(child) for child in getattr(node, "children", []))

    if name in {"strong", "b"}:
        value = _collapse_spaces(parts)
        return f"**{value}**" if value else ""
    if name in {"em", "i"}:
        value = _collapse_spaces(parts)
        return f"*{value}*" if value else ""
    if name == "br":
        return "\n"
    return parts
