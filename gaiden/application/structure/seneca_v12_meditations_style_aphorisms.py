from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


GLOSSARY_HEADING = "# Glossary"
CHAPTER_HEADING_RE = re.compile(r"^## Chapter (\d{2}) — .+$")
CHAPTER_PAGE_OPEN_RE = re.compile(r'^<div\s+class="chapter-page">\s*$')
SUBCHAPTER_OPEN_RE = re.compile(r'^<div\s+class="subchapter-block">\s*$')
SUBCHAPTER_MARKER_RE = re.compile(r'^<p class="subchapter-number">\d+</p>\s*$')
DIV_CLOSE_RE = re.compile(r"^</div>\s*$")
APHORISM_INLINE_RE = re.compile(r'<span class="aphorism-inline-number">\d+\.</span>\s*')
P_OPEN_RE = re.compile(r"^<p(?:\s+[^>]*)?>")
P_WRAPPER_RE = re.compile(r"^<p(?:\s+[^>]*)?>(.*)</p>$", re.DOTALL)
SOURCE_INTERNAL_HEADING_RE = re.compile(
    r"^#{3,6}\s+(?:\d+|Section|Aphorism|Book|Part)\b", re.IGNORECASE
)
ID_RE = re.compile(r'\bid="([^"]+)"')
HREF_RE = re.compile(r'\bhref="#([^"]+)"')

ENDNOTE_PATTERNS = [
    "# Endnotes",
    "Endnotes",
    "↩︎",
    "J. E. B. Mayor",
    "Koch declares",
    "Gertz reads",
    "Lipsius",
    "La Grange",
]


def _split_glossary(text: str, *, preserve_glossary: bool) -> tuple[str, str]:
    marker = "\n# Glossary\n"
    if marker in text:
        body, glossary = text.split(marker, 1)
        return body.rstrip(), "# Glossary\n" + glossary
    if preserve_glossary and text.startswith("# Glossary\n"):
        return "", text
    return text.rstrip(), ""


def _remove_old_inline_numbers(line: str) -> str:
    return APHORISM_INLINE_RE.sub("", line).strip()


def _is_wrapper_or_media(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("<div") or stripped == "</div>":
        return True
    if stripped.startswith(("<img", "<table", "</table", "<thead", "<tbody", "<tr", "<th", "<td")):
        return True
    return False


def _is_body_paragraph(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return False
    if _is_wrapper_or_media(stripped):
        return False
    if SUBCHAPTER_MARKER_RE.match(stripped):
        return False
    return True


def _number_paragraph(line: str, number: int, *, remove_old_inline_numbers: bool) -> str:
    stripped = line.strip()
    if remove_old_inline_numbers:
        stripped = _remove_old_inline_numbers(stripped)
    marker = f'<span class="aphorism-inline-number">{number:02d}.</span> '
    if stripped.startswith("<p") and stripped.endswith("</p>"):
        return re.sub(r"(<p(?:\s+[^>]*)?>)", rf"\1{marker}", stripped, count=1)
    return f"<p>{marker}{stripped}</p>"


def _transform_body(
    body: str,
    *,
    remove_subchapter_markers: bool,
    remove_old_inline_numbers: bool,
) -> tuple[str, dict[str, Any]]:
    lines = body.splitlines()
    out: list[str] = []
    chapter_counters: dict[str, int] = {}
    current_chapter: str | None = None
    in_chapter_page = False
    in_subchapter_block = False
    subchapter_blocks_removed = 0
    subchapter_markers_removed = 0
    old_inline_numbers_replaced = 0
    body_paragraphs_numbered = 0

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if remove_subchapter_markers and SUBCHAPTER_OPEN_RE.match(stripped):
            in_subchapter_block = True
            subchapter_blocks_removed += 1
            continue

        if remove_subchapter_markers and in_subchapter_block and DIV_CLOSE_RE.match(stripped):
            in_subchapter_block = False
            continue

        if remove_subchapter_markers and SUBCHAPTER_MARKER_RE.match(stripped):
            subchapter_markers_removed += 1
            continue

        if CHAPTER_PAGE_OPEN_RE.match(stripped):
            in_chapter_page = True
            out.append(stripped)
            continue

        if in_chapter_page:
            heading_match = CHAPTER_HEADING_RE.match(stripped)
            if heading_match:
                current_chapter = heading_match.group(1)
                chapter_counters[current_chapter] = 0
            out.append(stripped if stripped else "")
            if DIV_CLOSE_RE.match(stripped):
                in_chapter_page = False
            continue

        heading_match = CHAPTER_HEADING_RE.match(stripped)
        if heading_match:
            current_chapter = heading_match.group(1)
            chapter_counters[current_chapter] = 0
            out.append(stripped)
            continue

        if current_chapter and _is_body_paragraph(stripped):
            if APHORISM_INLINE_RE.search(stripped):
                old_inline_numbers_replaced += len(APHORISM_INLINE_RE.findall(stripped))
            chapter_counters[current_chapter] += 1
            out.append(
                _number_paragraph(
                    stripped,
                    chapter_counters[current_chapter],
                    remove_old_inline_numbers=remove_old_inline_numbers,
                )
            )
            body_paragraphs_numbered += 1
            continue

        out.append(stripped if stripped else "")

    return _collapse_blank_runs(out), {
        "chapter_paragraph_counts": chapter_counters,
        "body_paragraphs_numbered": body_paragraphs_numbered,
        "subchapter_blocks_removed": subchapter_blocks_removed,
        "subchapter_markers_removed": subchapter_markers_removed,
        "old_inline_numbers_replaced": old_inline_numbers_replaced,
    }


def _collapse_blank_runs(lines: list[str]) -> str:
    collapsed: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and previous_blank:
            continue
        collapsed.append(line)
        previous_blank = is_blank
    return "\n".join(collapsed).strip() + "\n"


def _duplicate_items(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def _validate_links(text: str) -> tuple[list[str], list[str]]:
    ids = ID_RE.findall(text)
    hrefs = HREF_RE.findall(text)
    id_set = set(ids)
    return _duplicate_items(ids), sorted({href for href in hrefs if href not in id_set})


def _endnote_residues(text: str) -> list[dict[str, Any]]:
    residues: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern in ENDNOTE_PATTERNS:
            if pattern in line:
                residues.append({"line": line_no, "pattern": pattern, "text": line.strip()})
                break
    return residues


def _plain_body_lines(text: str) -> list[str]:
    lines: list[str] = []
    in_subchapter_block = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if SUBCHAPTER_OPEN_RE.match(stripped):
            in_subchapter_block = True
            continue
        if in_subchapter_block and DIV_CLOSE_RE.match(stripped):
            in_subchapter_block = False
            continue
        if stripped in {'<div class="chapter-page">', "</div>"}:
            continue
        if CHAPTER_HEADING_RE.match(stripped) or stripped == "# Seneca’s Dialogues":
            continue
        if SUBCHAPTER_MARKER_RE.match(stripped):
            continue
        if stripped.startswith("#"):
            continue
        cleaned = _remove_old_inline_numbers(stripped)
        wrapper_match = P_WRAPPER_RE.match(cleaned)
        if wrapper_match:
            cleaned = wrapper_match.group(1).strip()
        lines.append(cleaned)
    return lines


def _chapter_sequences(
    text: str,
) -> tuple[dict[str, list[int]], dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
    sequences: dict[str, list[int]] = {}
    titles: dict[str, str] = {}
    unnumbered: list[dict[str, Any]] = []
    double_numbered: list[dict[str, Any]] = []
    current_chapter: str | None = None
    in_chapter_page = False

    for line_no, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if CHAPTER_PAGE_OPEN_RE.match(stripped):
            in_chapter_page = True
            continue
        if in_chapter_page:
            heading_match = CHAPTER_HEADING_RE.match(stripped)
            if heading_match:
                current_chapter = heading_match.group(1)
                titles[current_chapter] = stripped.split(" — ", 1)[1]
                sequences.setdefault(current_chapter, [])
            if DIV_CLOSE_RE.match(stripped):
                in_chapter_page = False
            continue

        heading_match = CHAPTER_HEADING_RE.match(stripped)
        if heading_match:
            current_chapter = heading_match.group(1)
            titles[current_chapter] = stripped.split(" — ", 1)[1]
            sequences.setdefault(current_chapter, [])
            continue

        if current_chapter and _is_body_paragraph(stripped):
            numbers = [int(value) for value in re.findall(r'<span class="aphorism-inline-number">(\d+)\.</span>', stripped)]
            if not numbers:
                unnumbered.append({"line": line_no, "chapter": current_chapter, "text": stripped[:160]})
            elif len(numbers) > 1:
                double_numbered.append({"line": line_no, "chapter": current_chapter, "numbers": numbers})
            else:
                sequences.setdefault(current_chapter, []).append(numbers[0])

    return sequences, titles, unnumbered, double_numbered


def _validate(output: str, input_body: str, glossary: str, original_glossary: str) -> dict[str, Any]:
    body_only = output.split("\n# Glossary\n", 1)[0]
    chapter_headings = [line.strip() for line in body_only.splitlines() if CHAPTER_HEADING_RE.match(line.strip())]
    sequences, chapter_titles, unnumbered, double_numbered = _chapter_sequences(body_only)
    sequence_errors: list[dict[str, Any]] = []
    for chapter, numbers in sequences.items():
        expected = list(range(1, len(numbers) + 1))
        if numbers != expected:
            sequence_errors.append({"chapter": chapter, "detected": numbers, "expected": expected})

    duplicate_ids, broken_hrefs = _validate_links(output)
    toc_polluting = [line for line in output.splitlines() if SOURCE_INTERNAL_HEADING_RE.match(line.strip())]
    residues = _endnote_residues(body_only)
    input_plain = _plain_body_lines(input_body)
    output_plain = _plain_body_lines(body_only)
    body_preserved = input_plain == output_plain
    glossary_preserved = glossary == original_glossary
    subchapter_blocks_remaining = [
        {"line": line_no, "text": line.strip()}
        for line_no, line in enumerate(output.splitlines(), start=1)
        if SUBCHAPTER_OPEN_RE.match(line.strip())
    ]
    subchapter_markers_remaining = [
        {"line": line_no, "text": line.strip()}
        for line_no, line in enumerate(output.splitlines(), start=1)
        if SUBCHAPTER_MARKER_RE.match(line.strip())
    ]
    glossary_numbered = bool(glossary and APHORISM_INLINE_RE.search(glossary))
    chapters = [
        {
            "chapter_number": chapter,
            "title": chapter_titles.get(chapter, ""),
            "aphorism_paragraphs": len(numbers),
            "first_aphorism": f"{numbers[0]:02d}" if numbers else None,
            "last_aphorism": f"{numbers[-1]:02d}" if numbers and numbers[-1] < 100 else (str(numbers[-1]) if numbers else None),
        }
        for chapter, numbers in sorted(sequences.items())
    ]
    passed = (
        len(chapter_headings) == 12
        and not unnumbered
        and not double_numbered
        and not sequence_errors
        and not subchapter_blocks_remaining
        and not subchapter_markers_remaining
        and not duplicate_ids
        and not broken_hrefs
        and not toc_polluting
        and not residues
        and glossary_preserved
        and body_preserved
        and not glossary_numbered
    )
    return {
        "passed": passed,
        "chapter_count": len(chapter_headings),
        "reader_chapter_count": len(chapter_headings),
        "chapter_headings": chapter_headings,
        "chapters": chapters,
        "chapter_aphorism_sequences": sequences,
        "sequence_errors": sequence_errors,
        "numbering_errors": sequence_errors,
        "unnumbered_body_paragraphs": unnumbered,
        "double_numbered_paragraphs": double_numbered,
        "subchapter_blocks_remaining": subchapter_blocks_remaining,
        "subchapter_markers_remaining": subchapter_markers_remaining,
        "toc_polluting_headings": toc_polluting,
        "duplicate_html_ids": duplicate_ids,
        "broken_hrefs": broken_hrefs,
        "endnotes_remaining": bool(residues),
        "endnote_residues": residues,
        "glossary_preserved": glossary_preserved,
        "glossary_numbered": glossary_numbered,
        "numbering_inside_glossary": glossary_numbered,
        "body_prose_preserved": body_preserved,
        "input_body_paragraphs": len(input_plain),
        "output_body_paragraphs": len(output_plain),
    }


def build_v12_meditations_style_aphorisms(
    text: str,
    *,
    preserve_glossary: bool = True,
    remove_subchapter_markers: bool = True,
    remove_old_inline_numbers: bool = True,
) -> tuple[str, dict[str, Any]]:
    body, glossary = _split_glossary(text, preserve_glossary=preserve_glossary)
    transformed_body, transform_report = _transform_body(
        body,
        remove_subchapter_markers=remove_subchapter_markers,
        remove_old_inline_numbers=remove_old_inline_numbers,
    )
    output = transformed_body.rstrip()
    if preserve_glossary and glossary:
        output += "\n\n" + glossary
    if not output.endswith("\n"):
        output += "\n"

    validation = _validate(output, body, glossary, glossary)
    report = {
        "summary": {
            "validation_passed": validation["passed"],
            "chapter_count": validation["chapter_count"],
            "reader_chapters": validation["reader_chapter_count"],
            "body_paragraphs_numbered": transform_report["body_paragraphs_numbered"],
            "total_aphorism_paragraphs_numbered": transform_report["body_paragraphs_numbered"],
            "subchapter_blocks_removed": transform_report["subchapter_blocks_removed"],
            "subchapter_markers_removed": transform_report["subchapter_markers_removed"],
            "old_inline_numbers_replaced": transform_report["old_inline_numbers_replaced"],
            "subchapter_blocks_remaining": len(validation["subchapter_blocks_remaining"]),
            "subchapter_markers_remaining": len(validation["subchapter_markers_remaining"]),
            "unnumbered_body_paragraphs": len(validation["unnumbered_body_paragraphs"]),
            "double_numbered_paragraphs": len(validation["double_numbered_paragraphs"]),
            "sequence_errors": len(validation["sequence_errors"]),
            "chapters_with_numbering_errors": len(validation["numbering_errors"]),
            "toc_polluting_headings": len(validation["toc_polluting_headings"]),
            "glossary_preserved": validation["glossary_preserved"],
            "glossary_links_valid": not validation["broken_hrefs"],
            "glossary_numbered": validation["glossary_numbered"],
            "endnotes_remaining": validation["endnotes_remaining"],
            "body_prose_preserved": validation["body_prose_preserved"],
        },
        "chapters": validation["chapters"],
        "transform": transform_report,
        "validation": validation,
    }
    return output, report


def _main() -> int:
    parser = argparse.ArgumentParser(description="Build Seneca Dialogues v12 Meditations-style aphorisms.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--no-preserve-glossary", action="store_true")
    parser.add_argument("--keep-subchapter-markers", action="store_true")
    parser.add_argument("--keep-old-inline-numbers", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report)
    output, report = build_v12_meditations_style_aphorisms(
        input_path.read_text(encoding="utf-8"),
        preserve_glossary=not args.no_preserve_glossary,
        remove_subchapter_markers=not args.keep_subchapter_markers,
        remove_old_inline_numbers=not args.keep_old_inline_numbers,
    )
    report = {"input": str(input_path), "output": str(output_path), **report}
    output_path.write_text(output, encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
