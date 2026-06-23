from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from editorial.models import EditionText

from . import edition_meta, paths

_TOC_HEADING_RE = re.compile(r"^\s*(#{1,6}\s+)?(contents|table of contents)\s*$", re.IGNORECASE)
_TOC_ENTRY_RE = re.compile(
    r"^\s*(contents|table of contents|introduction|bibliography|index|"
    r"book\s+[ivxlcdm]+|chapter\s+[ivxlcdm]+|part\s+[ivxlcdm]+|"
    r"book\s+\d+|chapter\s+\d+|part\s+\d+)\s*$",
    re.IGNORECASE,
)
_RULE_LINE_RE = re.compile(r"^\s*[-=]{5,}\s*$")
_DIV_MARKER_RE = re.compile(r"^\s*:::(?:\s+.*)?\s*$")
_MD_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")
_KEYWORD_HEADING_RE = re.compile(
    r"^\s*(chapter|book|part|section|adventure|cap[ií]tulo|kapitel)\b",
    re.IGNORECASE,
)
_ROMAN_DOT_RE = re.compile(
    r"^\s*(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV|XVI|XVII|XVIII|XIX|XX)\.\s+.+$",
    re.IGNORECASE,
)
_PG_BANNER_RE = re.compile(r"project gutenberg ebook", re.IGNORECASE)
_IMAGE_LINE_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]+\).*")


def _parse_book_id(book_code: str) -> int | None:
    try:
        if book_code.startswith("book_"):
            return int(book_code.split("_", 1)[1])
    except (ValueError, IndexError):
        return None
    m = re.search(r"(\d+)", book_code)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _out_dir_for_book_code(book_code: str) -> Path:
    book_id = _parse_book_id(book_code)
    if book_id is None:
        return paths.data_dir() / "chunks" / book_code / "heading_cleaner"
    return paths.data_dir() / "chunks" / f"book_{book_id:04d}" / "heading_cleaner"


def clean_path_for_book_code(book_code: str) -> Path:
    return _out_dir_for_book_code(book_code) / "clean.txt"


def report_path_for_book_code(book_code: str) -> Path:
    return _out_dir_for_book_code(book_code) / "heading_cleaner_report.json"


def _heading_key(stripped: str) -> str:
    md = _MD_HEADING_RE.match(stripped)
    if md:
        stripped = md.group(1)
    return re.sub(r"\s+", " ", stripped.strip().lower())


def _is_chapter_boundary(stripped: str) -> bool:
    if not stripped:
        return False
    if stripped.lower().startswith("::: chapter"):
        return True
    if _KEYWORD_HEADING_RE.match(stripped):
        return True
    if _ROMAN_DOT_RE.match(stripped):
        return True
    md = _MD_HEADING_RE.match(stripped)
    if not md:
        return False
    title = md.group(1).strip()
    return bool(_KEYWORD_HEADING_RE.match(title) or _ROMAN_DOT_RE.match(title))


def _is_body_boundary(stripped: str) -> bool:
    if not stripped:
        return False
    if stripped.lower().startswith("::: chapter"):
        return True
    if _ROMAN_DOT_RE.match(stripped):
        return True
    md = _MD_HEADING_RE.match(stripped)
    title = md.group(1).strip() if md else stripped
    return bool(
        re.match(r"^\s*(chapter|adventure|cap[ií]tulo|kapitel|section)\b", title, re.IGNORECASE)
    )


def _next_nonblank(lines: list[str], start: int) -> str:
    for idx in range(start, len(lines)):
        stripped = lines[idx].strip()
        if stripped:
            return stripped
    return ""


def _looks_like_body_prose(stripped: str) -> bool:
    if not stripped or len(stripped) < 35:
        return False
    return bool(re.search(r"[a-z]", stripped)) and not _TOC_ENTRY_RE.match(stripped)


def _is_probable_toc_entry(stripped: str) -> bool:
    if not stripped:
        return True
    if _TOC_ENTRY_RE.match(stripped):
        return True
    # Project Gutenberg TOCs often include the work title as a plain all-caps
    # line inside the contents block.
    letters = re.sub(r"[^A-Za-z]", "", stripped)
    return bool(letters) and letters.upper() == letters and len(stripped) <= 80


def _clean_normalized_text(text: str) -> tuple[str, dict[str, int]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    out: list[str] = []
    stats = {
        "removed_toc_blocks": 0,
        "removed_toc_lines": 0,
        "removed_rule_lines": 0,
        "removed_div_markers": 0,
        "removed_frontmatter_noise": 0,
        "deduped_headings": 0,
        "collapsed_blank_lines": 0,
    }

    seen_first_chapter = False
    prev_heading_key = ""
    blank_run = 0
    i = 0

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        if not seen_first_chapter and _TOC_HEADING_RE.match(stripped):
            j = i + 1
            while j < len(lines):
                probe = lines[j].strip()
                next_probe = _next_nonblank(lines, j + 1)
                if _is_probable_toc_entry(probe) and not _looks_like_body_prose(next_probe):
                    j += 1
                    continue
                if _looks_like_body_prose(next_probe) or _is_body_boundary(probe):
                    break
                j += 1
            stats["removed_toc_blocks"] += 1
            stats["removed_toc_lines"] += max(1, j - i)
            i = j
            continue

        if _is_body_boundary(stripped):
            seen_first_chapter = True

        if _RULE_LINE_RE.match(stripped):
            stats["removed_rule_lines"] += 1
            i += 1
            continue

        if not seen_first_chapter and (_PG_BANNER_RE.search(stripped) or _IMAGE_LINE_RE.match(stripped)):
            stats["removed_frontmatter_noise"] += 1
            i += 1
            continue

        if _DIV_MARKER_RE.match(stripped):
            stats["removed_div_markers"] += 1
            i += 1
            continue

        if stripped:
            heading_key = _heading_key(stripped)
            is_heading = _is_chapter_boundary(stripped)
            if is_heading and prev_heading_key and heading_key == prev_heading_key:
                stats["deduped_headings"] += 1
                i += 1
                continue
            if is_heading:
                prev_heading_key = heading_key
            blank_run = 0
        else:
            blank_run += 1
            if blank_run > 2:
                stats["collapsed_blank_lines"] += 1
                i += 1
                continue

        out.append(raw.rstrip())
        i += 1

    cleaned = "\n".join(out).strip()
    return (f"{cleaned}\n" if cleaned else ""), stats


def _get_normalized_text(edition) -> str:
    texts = EditionText.objects.filter(edition=edition).first()
    if texts and texts.normalized_path:
        path = Path(texts.normalized_path)
        if path.exists():
            return path.read_text(encoding="utf-8")
    if texts and texts.normalized_text:
        return texts.normalized_text
    raise ValueError("Normalize required: no normalized_text found.")


def run_heading_cleaner(edition, agent_name: str = "MechanicalHeadingCleaner") -> dict[str, object]:
    book_code = edition_meta.book_code(edition)
    normalized = _get_normalized_text(edition)
    cleaned, stats = _clean_normalized_text(normalized)

    out_dir = _out_dir_for_book_code(book_code)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    clean_path = out_dir / "clean.txt"
    clean_path.write_text(cleaned, encoding="utf-8")

    report_path = out_dir / "heading_cleaner_report.json"
    report = {
        "schema": "heading_cleaner_v2",
        "engine": "mechanical",
        "agent_name": agent_name,
        "book_code": book_code,
        "input_chars": len(normalized),
        "output_chars": len(cleaned),
        "output_dir": str(out_dir),
        "clean_path": str(clean_path),
        "stats": stats,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

    return {
        "agent_name": agent_name,
        "engine": "mechanical",
        "output_dir": str(out_dir),
        "clean_path": str(clean_path),
        "report_path": str(report_path),
        "stats": stats,
    }
