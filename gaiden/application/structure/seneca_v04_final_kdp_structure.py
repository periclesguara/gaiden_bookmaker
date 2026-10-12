from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


BOOK_TITLE = "Seneca’s Dialogues"
GLOSSARY_HEADING = "# Glossary"

CHAPTER_PAGE_OPEN = '<div class="chapter-page">'
PART_PAGE_OPEN_RE = re.compile(r'^<div\s+class="part-page">\s*$')
DIV_CLOSE_RE = re.compile(r"^</div>\s*$")
PART_HEADING_RE = re.compile(r"^##\s+Part\s+[IVXLCDM]+\s+—\s+(.+?)\s*$", re.IGNORECASE)
SOURCE_HEADING_RE = re.compile(r"^(#{3,6})\s+(Chapter|Book|Part|Section|Aphorism)\b.*$", re.IGNORECASE)
ANY_HEADING_RE = re.compile(r"^#{1,6}\s+.+")
ENDNOTES_RE = re.compile(r"^#?\s*(?:Endnotes|Notes|Translator.*Notes)\b", re.IGNORECASE)
SUP_RE = re.compile(r"<sup\b[^>]*>.*?</sup>")
ID_RE = re.compile(r'\bid="([^"]+)"')
HREF_RE = re.compile(r'\bhref="#([^"]+)"')
APHORISM_RE = re.compile(r'^<p class="aphorism-number">(\d+)</p>$')

SPELLING_REPLACEMENTS = {
    "splendour": "splendor",
    "behaviour": "behavior",
    "valour": "valor",
    "saviour": "savior",
    "Splendour": "Splendor",
    "Behaviour": "Behavior",
    "Valour": "Valor",
    "Saviour": "Savior",
}


def _split_glossary(text: str, *, preserve_glossary: bool) -> tuple[str, str]:
    marker = "\n# Glossary\n"
    if marker in text:
        body, glossary = text.split(marker, 1)
        return body.rstrip(), "# Glossary\n" + glossary.strip()
    if preserve_glossary and text.startswith("# Glossary\n"):
        return "", text.strip()
    return text.rstrip(), ""


def _remove_endnotes(text: str) -> tuple[str, dict[str, Any]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if ENDNOTES_RE.match(line.strip()):
            return "\n".join(lines[:index]).rstrip(), {
                "removed": True,
                "start_line": index + 1,
                "line_count": len(lines) - index,
            }
    return text.rstrip(), {"removed": False, "start_line": 0, "line_count": 0}


def _apply_us_spelling(body: str) -> tuple[str, int]:
    replacements = 0
    corrected = body
    for source, target in SPELLING_REPLACEMENTS.items():
        corrected, count = re.subn(rf"\b{re.escape(source)}\b", target, corrected)
        replacements += count
    return corrected, replacements


def _strip_heading_markers(title: str) -> tuple[str, list[str]]:
    markers = SUP_RE.findall(title)
    clean = SUP_RE.sub("", title).strip()
    clean = re.sub(r"\s+", " ", clean)
    return clean, markers


def _chapter_heading(number: int, title: str) -> str:
    return f"## Chapter {number:02d} — {title}"


def _append_blank(out: list[str]) -> None:
    if out and out[-1] != "":
        out.append("")


def _append_chapter_page(
    out: list[str],
    *,
    chapter_number: int,
    title: str,
    use_chapter_page_divs: bool,
) -> None:
    _append_blank(out)
    if use_chapter_page_divs:
        out.append(CHAPTER_PAGE_OPEN)
        out.append("")
    out.append(_chapter_heading(chapter_number, title))
    if use_chapter_page_divs:
        out.append("")
        out.append("</div>")
    out.append("")


def _append_internal_marker(out: list[str], block_number: int) -> None:
    _append_blank(out)
    out.append(f'<p class="aphorism-number">{block_number:02d}</p>')
    out.append("")


def _cleanup_blank_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    blank_count = 0
    for line in lines:
        if line == "":
            blank_count += 1
            if blank_count <= 2:
                cleaned.append(line)
            continue
        blank_count = 0
        cleaned.append(line)
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return cleaned


def _duplicate_items(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def _source_prose_lines(text: str) -> list[str]:
    lines: list[str] = []
    in_part_page = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if PART_PAGE_OPEN_RE.match(stripped):
            in_part_page = True
            continue
        if in_part_page:
            if DIV_CLOSE_RE.match(stripped):
                in_part_page = False
            continue
        if not stripped or stripped == f"# {BOOK_TITLE}":
            continue
        if PART_HEADING_RE.match(stripped) or SOURCE_HEADING_RE.match(stripped):
            continue
        if ENDNOTES_RE.match(stripped):
            break
        lines.append(raw.rstrip())
    return lines


def _body_prose_preserved(source_body: str, output: str) -> bool:
    return all(line in output for line in _source_prose_lines(source_body))


def _flatten_body(
    body: str,
    *,
    use_chapter_page_divs: bool,
) -> tuple[str, dict[str, Any]]:
    out: list[str] = [f"# {BOOK_TITLE}", ""]
    chapters: list[dict[str, Any]] = []
    counts = {
        "source_chapter_headings_removed": 0,
        "book_headings_removed": 0,
        "part_headings_removed": 0,
        "section_headings_removed": 0,
        "aphorism_headings_removed": 0,
        "heading_glossary_markers_removed": 0,
    }
    in_part_page = False
    chapter_number = 0
    block_number = 0
    pending_internal_marker = False
    pending_heading_markers: list[str] = []

    for raw in body.splitlines():
        line = raw.rstrip()
        stripped = line.strip()

        if PART_PAGE_OPEN_RE.match(stripped):
            in_part_page = True
            continue

        if in_part_page and DIV_CLOSE_RE.match(stripped):
            in_part_page = False
            continue

        part_match = PART_HEADING_RE.match(stripped)
        if part_match:
            title, removed_markers = _strip_heading_markers(part_match.group(1))
            chapter_number += 1
            block_number = 0
            pending_internal_marker = True
            pending_heading_markers.extend(removed_markers)
            counts["part_headings_removed"] += 1
            counts["heading_glossary_markers_removed"] += len(removed_markers)
            _append_chapter_page(
                out,
                chapter_number=chapter_number,
                title=title,
                use_chapter_page_divs=use_chapter_page_divs,
            )
            chapters.append({"chapter_number": f"{chapter_number:02d}", "title": title, "internal_blocks": 0})
            continue

        if in_part_page:
            continue

        if not stripped:
            _append_blank(out)
            continue

        if stripped == f"# {BOOK_TITLE}":
            continue

        source_heading_match = SOURCE_HEADING_RE.match(stripped)
        if source_heading_match:
            kind = source_heading_match.group(2).lower()
            if kind == "chapter":
                counts["source_chapter_headings_removed"] += 1
            elif kind == "book":
                counts["book_headings_removed"] += 1
            elif kind == "part":
                counts["part_headings_removed"] += 1
            elif kind == "section":
                counts["section_headings_removed"] += 1
            elif kind == "aphorism":
                counts["aphorism_headings_removed"] += 1
            pending_internal_marker = True
            continue

        if pending_internal_marker:
            block_number += 1
            _append_internal_marker(out, block_number)
            if chapters:
                chapters[-1]["internal_blocks"] += 1
            pending_internal_marker = False

        if pending_heading_markers:
            line += "".join(pending_heading_markers)
            pending_heading_markers = []

        out.append(line)

    return "\n".join(_cleanup_blank_lines(out)).strip() + "\n", {
        "chapters": chapters,
        "counts": counts,
    }


def _empty_internal_blocks(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    empty: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not APHORISM_RE.match(line.strip()):
            continue
        next_index = index + 1
        while next_index < len(lines) and not lines[next_index].strip():
            next_index += 1
        if next_index >= len(lines) or ANY_HEADING_RE.match(lines[next_index].strip()) or APHORISM_RE.match(
            lines[next_index].strip()
        ):
            empty.append({"line": index + 1, "text": line.strip()})
    return empty


def _chapter_marker_sequences(text: str) -> tuple[bool, list[dict[str, Any]]]:
    current: list[int] = []
    chapter = ""
    errors: list[dict[str, Any]] = []

    def flush() -> None:
        if not chapter:
            return
        expected = list(range(1, len(current) + 1))
        if current != expected:
            errors.append({"chapter": chapter, "numbers": current, "expected": expected})

    for line in text.splitlines():
        if re.match(r"^## Chapter \d{2} — .+$", line):
            flush()
            chapter = line
            current = []
            continue
        marker = APHORISM_RE.match(line.strip())
        if marker:
            current.append(int(marker.group(1)))
    flush()
    return not errors, errors


def _validate(
    output: str,
    *,
    source_body: str,
    preserve_glossary: bool,
) -> dict[str, Any]:
    reader_chapters = re.findall(r"^## Chapter \d{2} — .+$", output, flags=re.MULTILINE)
    headings = [line for line in output.splitlines() if ANY_HEADING_RE.match(line.strip())]
    allowed_heading_re = re.compile(r"^(# Seneca’s Dialogues|## Chapter \d{2} — .+|# Glossary)$")
    source_headings_remaining = [line for line in headings if not allowed_heading_re.match(line.strip())]
    internal_markdown_headings = [
        line
        for line in output.splitlines()
        if re.match(r"^#{3,6}\s+\d+\s*$", line.strip())
        or re.match(r"^#{3,6}\s+(?:Part|Book|Chapter|Section|Aphorism)\b", line.strip(), flags=re.IGNORECASE)
    ]
    ids = ID_RE.findall(output)
    hrefs = HREF_RE.findall(output)
    id_set = set(ids)
    duplicate_ids = _duplicate_items(ids)
    broken_hrefs = sorted({href for href in hrefs if href not in id_set})
    headings_with_markers = [line for line in headings if "<sup" in line]
    roman_headings = [
        line
        for line in reader_chapters
        if re.search(r"\b(?:[IVXLCDM]{2,}|IV|IX|XL|XC|CD|CM)\b", line)
    ]
    empty_blocks = _empty_internal_blocks(output)
    numbering_valid, numbering_errors = _chapter_marker_sequences(output)
    glossary_count = len(re.findall(r"^# Glossary$", output, flags=re.MULTILINE))
    endnotes_remaining = bool(ENDNOTES_RE.search(output) or "↩︎" in output)
    prose_preserved = _body_prose_preserved(source_body, output)
    passed = (
        len(reader_chapters) == 12
        and not source_headings_remaining
        and not internal_markdown_headings
        and not duplicate_ids
        and not broken_hrefs
        and not headings_with_markers
        and not roman_headings
        and not empty_blocks
        and numbering_valid
        and not endnotes_remaining
        and (not preserve_glossary or glossary_count == 1)
        and prose_preserved
    )
    return {
        "passed": passed,
        "reader_chapter_count": len(reader_chapters),
        "source_headings_remaining": source_headings_remaining,
        "internal_markdown_headings_remaining": internal_markdown_headings,
        "internal_numbering_valid": numbering_valid,
        "internal_numbering_errors": numbering_errors,
        "duplicate_html_ids": duplicate_ids,
        "broken_hrefs": broken_hrefs,
        "headings_with_glossary_markers": headings_with_markers,
        "reader_headings_with_roman_numerals": roman_headings,
        "empty_internal_blocks": empty_blocks,
        "endnotes_remaining": endnotes_remaining,
        "glossary_count": glossary_count,
        "glossary_preserved": glossary_count == 1,
        "body_prose_preserved": prose_preserved,
    }


def build_v04_final_kdp_structure(
    text: str,
    *,
    remove_endnotes: bool = True,
    preserve_glossary: bool = True,
    use_chapter_page_divs: bool = True,
    fix_us_spelling: bool = False,
) -> tuple[str, dict[str, Any]]:
    body, glossary = _split_glossary(text, preserve_glossary=preserve_glossary)
    source_body_for_validation = body
    endnotes_report = {"removed": False, "start_line": 0, "line_count": 0}
    glossary_endnotes_report = {"removed": False, "start_line": 0, "line_count": 0}
    if remove_endnotes:
        body, endnotes_report = _remove_endnotes(body)
        if glossary:
            glossary, glossary_endnotes_report = _remove_endnotes(glossary)
    spelling_replacements = 0
    if fix_us_spelling:
        body, spelling_replacements = _apply_us_spelling(body)

    flattened, structure_report = _flatten_body(body, use_chapter_page_divs=use_chapter_page_divs)
    output = flattened.rstrip()
    if preserve_glossary and glossary:
        output += "\n\n" + glossary.strip()
    output += "\n"

    validation = _validate(
        output,
        source_body=source_body_for_validation,
        preserve_glossary=preserve_glossary and bool(glossary),
    )
    counts = structure_report["counts"]
    report = {
        "summary": {
            "validation_passed": validation["passed"],
            "reader_chapters_created": validation["reader_chapter_count"],
            "internal_numbered_blocks_created": sum(
                int(chapter["internal_blocks"]) for chapter in structure_report["chapters"]
            ),
            "source_chapter_headings_removed": counts["source_chapter_headings_removed"],
            "book_headings_removed": counts["book_headings_removed"],
            "part_headings_removed": counts["part_headings_removed"],
            "section_headings_removed": counts["section_headings_removed"],
            "internal_markers_as_markdown_headings": len(validation["internal_markdown_headings_remaining"]),
            "heading_glossary_markers_removed": counts["heading_glossary_markers_removed"],
            "endnotes_removed": endnotes_report["removed"] or glossary_endnotes_report["removed"],
            "glossary_preserved": validation["glossary_preserved"],
            "glossary_links_valid": not validation["broken_hrefs"],
            "body_prose_preserved": validation["body_prose_preserved"],
            "spelling_replacements": spelling_replacements,
        },
        "chapters": structure_report["chapters"],
        "removed_endnotes": {
            "body": endnotes_report,
            "glossary": glossary_endnotes_report,
        },
        "validation": validation,
    }
    return output, report


def _main() -> int:
    parser = argparse.ArgumentParser(description="Build Seneca Dialogues v04 final KDP structure.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--no-remove-endnotes", action="store_true")
    parser.add_argument("--no-preserve-glossary", action="store_true")
    parser.add_argument("--no-chapter-page-divs", action="store_true")
    parser.add_argument("--fix-us-spelling", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report)
    output, report = build_v04_final_kdp_structure(
        input_path.read_text(encoding="utf-8"),
        remove_endnotes=not args.no_remove_endnotes,
        preserve_glossary=not args.no_preserve_glossary,
        use_chapter_page_divs=not args.no_chapter_page_divs,
        fix_us_spelling=args.fix_us_spelling,
    )
    report = {"input": str(input_path), "output": str(output_path), **report}
    output_path.write_text(output, encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
