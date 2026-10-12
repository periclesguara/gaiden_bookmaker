from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


BOOK_TITLE = "Seneca’s Dialogues"
GLOSSARY_HEADING = "# Glossary"

MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
RAW_PART_RE = re.compile(r"^#{0,6}\s*Part\s+(\d+)\s*$", re.IGNORECASE)
NAKED_BOOK_RE = re.compile(r"^###\s+Book\s*$", re.IGNORECASE)
BOOK_HEADING_RE = re.compile(r"^###\s+Book\s+([IVXLCDM]+|\d+)\s*$", re.IGNORECASE)
CHAPTER_HEADING_RE = re.compile(r"^#{3,6}\s+Chapter\s+(\d+)\s*$", re.IGNORECASE)
ENDNOTES_RE = re.compile(r"^#?\s*(?:Endnotes|Notes|Translator.*Notes)\b", re.IGNORECASE)
ID_RE = re.compile(r'\bid="([^"]+)"')
HREF_RE = re.compile(r'\bhref="#([^"]+)"')
SUP_RE = re.compile(r"<sup\b.*?</sup>")

TREATISE_TITLES = {
    "To Marcia, on Consolation": "To Marcia, on Consolation",
    "On Anger": "On Anger",
    "To Helvia, on Consolation": "To Helvia, on Consolation",
    "To Polybius, on Consolation": "To Polybius, on Consolation",
    "On the Shortness of Life": "On the Shortness of Life",
    "On Leisure": "On Leisure",
    "On Peace of Mind": "On Peace of Mind",
    "On Providence": "On Providence",
    "On the Firmness of the Wise Person": "On the Firmness of the Wise Person",
    "On a Happy Life": "On the Happy Life",
    "On the Happy Life": "On the Happy Life",
    "On Clemency": "On Clemency",
    "On Benefits": "On Benefits",
}

ROMANS = [
    "I",
    "II",
    "III",
    "IV",
    "V",
    "VI",
    "VII",
    "VIII",
    "IX",
    "X",
    "XI",
    "XII",
    "XIII",
    "XIV",
    "XV",
    "XVI",
    "XVII",
    "XVIII",
    "XIX",
    "XX",
    "XXI",
]


def _roman(number: int) -> str:
    if 1 <= number <= len(ROMANS):
        return ROMANS[number - 1]
    values = [
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ]
    out = []
    remainder = number
    for value, glyph in values:
        while remainder >= value:
            out.append(glyph)
            remainder -= value
    return "".join(out)


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


def _plain_heading(text: str) -> str:
    return SUP_RE.sub("", text).strip()


def _part_title_from_heading(text: str) -> str | None:
    return TREATISE_TITLES.get(_plain_heading(text))


def _recipient_from_line(line: str) -> str | None:
    stripped = line.strip().strip("_").strip()
    stripped = re.sub(r"\s+\([^)]+\)\s*$", "", stripped).strip()
    if re.match(r"^To\s+[A-Z][A-Za-z ]+\.?$", stripped):
        return stripped.rstrip(".") + "."
    return None


def _find_recipient(lines: list[str], start: int) -> tuple[str | None, int]:
    index = start + 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines):
        return None, start
    recipient = _recipient_from_line(lines[index])
    if recipient:
        return recipient, index
    return None, start


def _append_blank(out: list[str]) -> None:
    if out and out[-1] != "":
        out.append("")


def _append_part_page(
    out: list[str],
    *,
    part_no: int,
    heading_text: str,
    canonical_title: str,
    recipient: str | None,
    use_part_page_divs: bool,
) -> None:
    _append_blank(out)
    if use_part_page_divs:
        out.append('<div class="part-page">')
        out.append("")
    out.append(f"## Part {_roman(part_no)} — {heading_text}")
    if recipient:
        out.append("")
        out.append(f"_{recipient}_")
    if use_part_page_divs:
        out.append("")
        out.append("</div>")
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


def _validate(text: str, *, expected_parts: int, preserve_glossary: bool) -> dict[str, Any]:
    ids = ID_RE.findall(text)
    hrefs = HREF_RE.findall(text)
    id_set = set(ids)
    broken_hrefs = sorted({href for href in hrefs if href not in id_set})
    part_headings = re.findall(r"^## Part [IVXLCDM]+ — .+$", text, flags=re.MULTILINE)
    raw_part_markers = [
        line
        for line in text.splitlines()
        if RAW_PART_RE.match(line.strip()) and not line.strip().startswith("## Part ")
    ]
    naked_books = [line for line in text.splitlines() if NAKED_BOOK_RE.match(line.strip())]
    endnotes_remaining = [line for line in text.splitlines() if ENDNOTES_RE.match(line.strip())]
    glossary_count = len(re.findall(r"^# Glossary$", text, flags=re.MULTILINE))
    missing_parts = [_roman(number) for number in range(len(part_headings) + 1, expected_parts + 1)]
    passed = (
        len(part_headings) == expected_parts
        and not raw_part_markers
        and not naked_books
        and not endnotes_remaining
        and not _duplicate_items(ids)
        and not broken_hrefs
        and (not preserve_glossary or glossary_count == 1)
    )
    return {
        "passed": passed,
        "part_count": len(part_headings),
        "expected_parts": expected_parts,
        "missing_parts": missing_parts,
        "part_count_matches_expected": len(part_headings) == expected_parts,
        "glossary_count": glossary_count,
        "glossary_preserved": glossary_count == 1,
        "duplicate_html_ids": _duplicate_items(ids),
        "broken_hrefs": broken_hrefs,
        "raw_part_markers_remaining": raw_part_markers,
        "naked_book_headings_remaining": naked_books,
        "endnotes_remaining": endnotes_remaining,
    }


def _build_body(
    body: str,
    *,
    use_part_page_divs: bool,
) -> tuple[str, dict[str, Any]]:
    lines = body.splitlines()
    out: list[str] = [f"# {BOOK_TITLE}", ""]
    parts: list[dict[str, Any]] = []
    in_book = False
    current_part_title = ""
    book_count = 0
    section_count = 0
    awaiting_book_part_marker = False
    inferred: list[dict[str, Any]] = []
    skipped_raw_parts: list[dict[str, Any]] = []
    index = 0

    while index < len(lines):
        raw = lines[index].rstrip()
        stripped = raw.strip()
        line_no = index + 1

        if not stripped:
            _append_blank(out)
            index += 1
            continue

        if stripped == f"# {BOOK_TITLE}":
            index += 1
            continue

        if stripped == "On a Happy Life" and index + 1 < len(lines):
            recipient = _recipient_from_line(lines[index + 1]) if index + 1 < len(lines) else None
            if recipient:
                part_no = len(parts) + 1
                _append_part_page(
                    out,
                    part_no=part_no,
                    heading_text="On the Happy Life",
                    canonical_title="On the Happy Life",
                    recipient=recipient,
                    use_part_page_divs=use_part_page_divs,
                )
                parts.append({"part": _roman(part_no), "title": "On the Happy Life", "source_line": line_no})
                inferred.append(
                    {
                        "line": line_no,
                        "from": "On a Happy Life / To Gallio.",
                        "to": "Part heading with recipient",
                        "reason": "plain_title_promoted",
                    }
                )
                current_part_title = "On the Happy Life"
                book_count = 0
                section_count = 0
                in_book = False
                awaiting_book_part_marker = False
                index += 2
                continue

        heading_match = MARKDOWN_HEADING_RE.match(stripped)
        if heading_match:
            hashes, heading_text = heading_match.groups()
            title = _part_title_from_heading(heading_text)
            if hashes == "##" and title:
                recipient, consumed_index = _find_recipient(lines, index)
                part_no = len(parts) + 1
                _append_part_page(
                    out,
                    part_no=part_no,
                    heading_text=heading_text,
                    canonical_title=title,
                    recipient=recipient,
                    use_part_page_divs=use_part_page_divs,
                )
                parts.append({"part": _roman(part_no), "title": title, "source_line": line_no})
                current_part_title = title
                book_count = 0
                section_count = 0
                in_book = False
                awaiting_book_part_marker = False
                index = consumed_index + 1
                continue

            if NAKED_BOOK_RE.match(stripped):
                book_count += 1
                _append_blank(out)
                out.append(f"### Book {_roman(book_count)}")
                out.append("")
                inferred.append(
                    {
                        "line": line_no,
                        "from": stripped,
                        "to": f"### Book {_roman(book_count)}",
                        "reason": "naked_book_numbered_from_sequence",
                        "part": current_part_title,
                    }
                )
                in_book = True
                section_count = 0
                awaiting_book_part_marker = True
                index += 1
                continue

            raw_part_match = RAW_PART_RE.match(stripped)
            if raw_part_match:
                raw_part = int(raw_part_match.group(1))
                if awaiting_book_part_marker:
                    skipped_raw_parts.append(
                        {
                            "line": line_no,
                            "text": stripped,
                            "reason": "raw_part_marker_attached_to_preceding_book",
                        }
                    )
                    awaiting_book_part_marker = False
                    index += 1
                    continue
                section_count += 1
                level = "####" if in_book else "###"
                _append_blank(out)
                out.append(f"{level} Section {section_count}")
                out.append("")
                inferred.append(
                    {
                        "line": line_no,
                        "from": stripped,
                        "to": f"{level} Section {section_count}",
                        "reason": "raw_part_marker_demoted_to_reader_section",
                        "source_part": raw_part,
                        "part": current_part_title,
                    }
                )
                in_book = False
                awaiting_book_part_marker = False
                index += 1
                continue

            chapter_match = CHAPTER_HEADING_RE.match(stripped)
            if chapter_match:
                _append_blank(out)
                if in_book:
                    out.append(f"#### Chapter {chapter_match.group(1)}")
                else:
                    out.append(f"### Chapter {chapter_match.group(1)}")
                out.append("")
                awaiting_book_part_marker = False
                index += 1
                continue

            out.append(raw)
            awaiting_book_part_marker = False
            index += 1
            continue

        out.append(raw)
        awaiting_book_part_marker = False
        index += 1

    clean_lines = _cleanup_blank_lines(out)
    return "\n".join(clean_lines).strip() + "\n", {
        "parts": parts,
        "inferred_markers": inferred,
        "skipped_raw_part_markers": skipped_raw_parts,
    }


def build_v03_editorial_hierarchy(
    text: str,
    *,
    expected_parts: int = 14,
    remove_endnotes: bool = True,
    preserve_glossary: bool = True,
    use_part_page_divs: bool = True,
    fix_us_spelling: bool = False,
) -> tuple[str, dict[str, Any]]:
    if fix_us_spelling:
        raise ValueError("fix_us_spelling is intentionally disabled for v03 structural hierarchy.")

    body, glossary = _split_glossary(text, preserve_glossary=preserve_glossary)
    endnotes_report = {"removed": False, "start_line": 0, "line_count": 0}
    if remove_endnotes:
        body, endnotes_report = _remove_endnotes(body)

    structured_body, structure_report = _build_body(body, use_part_page_divs=use_part_page_divs)
    output = structured_body.rstrip()
    if preserve_glossary and glossary:
        output += "\n\n" + glossary.strip()
    output += "\n"

    validation = _validate(
        output,
        expected_parts=expected_parts,
        preserve_glossary=preserve_glossary and bool(glossary),
    )
    report = {
        "summary": {
            "validation_passed": validation["passed"],
            "part_count": validation["part_count"],
            "expected_parts": expected_parts,
            "part_count_matches_expected": validation["part_count_matches_expected"],
            "books_inferred": sum(1 for item in structure_report["inferred_markers"] if "Book" in item["to"]),
            "sections_inferred": sum(1 for item in structure_report["inferred_markers"] if "Section" in item["to"]),
            "glossary_preserved": validation["glossary_preserved"],
            "glossary_links_valid": not validation["broken_hrefs"],
            "endnotes_removed": endnotes_report["removed"],
            "raw_part_markers_remaining": len(validation["raw_part_markers_remaining"]),
            "naked_book_headings_remaining": len(validation["naked_book_headings_remaining"]),
        },
        "parts": structure_report["parts"],
        "inferred_markers": structure_report["inferred_markers"],
        "skipped_raw_part_markers": structure_report["skipped_raw_part_markers"],
        "removed_endnotes": endnotes_report,
        "validation": validation,
    }
    return output, report


def _main() -> int:
    parser = argparse.ArgumentParser(description="Build Seneca Dialogues v03 editorial hierarchy.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--expected-parts", type=int, default=14)
    parser.add_argument("--use-part-page-divs", action="store_true")
    parser.add_argument("--no-remove-endnotes", action="store_true")
    parser.add_argument("--no-preserve-glossary", action="store_true")
    parser.add_argument("--fix-us-spelling", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report)

    output, report = build_v03_editorial_hierarchy(
        input_path.read_text(encoding="utf-8"),
        expected_parts=args.expected_parts,
        remove_endnotes=not args.no_remove_endnotes,
        preserve_glossary=not args.no_preserve_glossary,
        use_part_page_divs=args.use_part_page_divs,
        fix_us_spelling=args.fix_us_spelling,
    )
    report = {"input": str(input_path), "output": str(output_path), **report}
    output_path.write_text(output, encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
