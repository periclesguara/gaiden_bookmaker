from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any


GLOSSARY_HEADING = "# Glossary"
CHAPTER_HEADING_RE = re.compile(r"^## Chapter \d{2} — .+$")
CHAPTER_PAGE_OPEN_RE = re.compile(r'^<div\s+class="chapter-page">\s*$')
EPILOGUE_PAGE_OPEN_RE = re.compile(r'^<div\s+class="epilogue-section-page">\s*$')
SUBCHAPTER_OPEN_RE = re.compile(r'^<div\s+class="subchapter-block">\s*$')
DIV_CLOSE_RE = re.compile(r"^</div>\s*$")
APHORISM_MARKER_RE = re.compile(r'^<p class="aphorism-number">(\d+)</p>$')
SUBCHAPTER_MARKER_RE = re.compile(r'^<p class="subchapter-number">(\d+)</p>$')
APHORISM_INLINE_RE = re.compile(r'<span class="aphorism-inline-number">(\d+)\.</span>')
ANY_HEADING_RE = re.compile(r"^#{1,6}\s+.+")
SOURCE_INTERNAL_HEADING_RE = re.compile(r"^#{3,6}\s+(?:\d+|Section|Aphorism|Book|Part)\b", re.IGNORECASE)
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

EPILOGUE_TITLES = {
    "Epilogue",
    "The Philosophical Lineage of Seneca",
    "Stoicism",
    "Attalus",
    "The Cynics",
    "Demetrius the Cynic",
    "Pythagorean Influence and the School of the Sextii",
    "Sotion",
    "Papirius Fabianus",
    "Socrates",
    "Plato",
    "Epicurus and the Epicureans",
    "The Roman Tradition",
    "Seneca’s Own Synthesis",
    "Why Seneca Still Matters",
}

REQUIRED_CSS = """
.chapter-page {
  page-break-before: always;
  break-before: page;
  page-break-after: always;
  break-after: page;
  text-align: center;
  margin-top: 30%;
}

.epilogue-section-page {
  page-break-before: always;
  break-before: page;
  page-break-after: always;
  break-after: page;
  text-align: center;
  margin-top: 30%;
}

.subchapter-block {
  page-break-inside: avoid;
  break-inside: avoid;
}

.subchapter-number {
  text-align: center;
  font-weight: bold;
  margin-top: 1.5em;
  margin-bottom: 1em;
}

.aphorism-inline-number {
  font-weight: bold;
}
""".strip()


def _split_glossary(text: str, *, preserve_glossary: bool) -> tuple[str, str]:
    marker = "\n# Glossary\n"
    if marker in text:
        body, glossary = text.split(marker, 1)
        return body.rstrip(), "# Glossary\n" + glossary
    if preserve_glossary and text.startswith("# Glossary\n"):
        return "", text
    return text.rstrip(), ""


def _append_blank(out: list[str]) -> None:
    if out and out[-1] != "":
        out.append("")


def _wrap_chapter_heading(heading: str) -> list[str]:
    return ['<div class="chapter-page">', "", heading, "", "</div>"]


def _wrap_epilogue_heading(title: str) -> list[str]:
    return ['<div class="epilogue-section-page">', "", f"## {title}", "", "</div>"]


def _heading_title(line: str) -> tuple[str, str] | None:
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
    if not match:
        return None
    return match.group(1), match.group(2).strip()


def _next_nonblank(lines: list[str], start: int) -> int | None:
    index = start
    while index < len(lines):
        if lines[index].strip():
            return index
        index += 1
    return None


def _paragraph_with_inline_number(number: str, paragraph: str) -> str:
    stripped = paragraph.strip()
    if APHORISM_INLINE_RE.search(stripped):
        return stripped
    if stripped.startswith("<p") and stripped.endswith("</p>"):
        return re.sub(
            r"(<p(?:\s+[^>]*)?>)",
            rf'\1<span class="aphorism-inline-number">{number}.</span> ',
            stripped,
            count=1,
        )
    return f'<p><span class="aphorism-inline-number">{number}.</span> {stripped}</p>'


def _wrap_subchapter_block(number: str, paragraph: str) -> list[str]:
    return [
        '<div class="subchapter-block">',
        "",
        f'<p class="subchapter-number">{number}</p>',
        "",
        _paragraph_with_inline_number(number, paragraph),
        "",
        "</div>",
    ]


def _transform_body(
    body: str,
    *,
    wrap_epilogue_sections: bool,
    wrap_subchapter_first_paragraph: bool,
) -> tuple[str, dict[str, Any]]:
    lines = body.splitlines()
    out: list[str] = []
    chapter_pages_wrapped = 0
    epilogue_pages_wrapped = 0
    subchapter_blocks_wrapped = 0
    index = 0

    while index < len(lines):
        raw = lines[index].rstrip()
        stripped = raw.strip()

        if CHAPTER_PAGE_OPEN_RE.match(stripped):
            next_index = _next_nonblank(lines, index + 1)
            if next_index is not None and CHAPTER_HEADING_RE.match(lines[next_index].strip()):
                close_index = next_index + 1
                while close_index < len(lines) and not DIV_CLOSE_RE.match(lines[close_index].strip()):
                    close_index += 1
                moved_body = [
                    line.rstrip()
                    for line in lines[index + 1 : close_index]
                    if line.strip() and not CHAPTER_HEADING_RE.match(line.strip())
                ]
                out.extend(_wrap_chapter_heading(lines[next_index].strip()))
                if moved_body:
                    out.append("")
                    out.extend(moved_body)
                chapter_pages_wrapped += 1
                index = close_index + 1 if close_index < len(lines) else next_index + 1
                continue

        if CHAPTER_HEADING_RE.match(stripped):
            out.extend(_wrap_chapter_heading(stripped))
            chapter_pages_wrapped += 1
            index += 1
            continue

        if wrap_epilogue_sections:
            heading = _heading_title(stripped)
            if heading and heading[1] in EPILOGUE_TITLES and not CHAPTER_HEADING_RE.match(stripped):
                out.extend(_wrap_epilogue_heading(heading[1]))
                epilogue_pages_wrapped += 1
                index += 1
                continue

        marker_match = APHORISM_MARKER_RE.match(stripped) or SUBCHAPTER_MARKER_RE.match(stripped)
        if marker_match and wrap_subchapter_first_paragraph:
            first_paragraph_index = _next_nonblank(lines, index + 1)
            if first_paragraph_index is not None:
                first_paragraph = lines[first_paragraph_index].rstrip()
                if not ANY_HEADING_RE.match(first_paragraph.strip()) and not DIV_CLOSE_RE.match(first_paragraph.strip()):
                    out.extend(_wrap_subchapter_block(marker_match.group(1), first_paragraph))
                    subchapter_blocks_wrapped += 1
                    index = first_paragraph_index + 1
                    continue

        out.append(raw)
        index += 1

    return "\n".join(out).strip() + "\n", {
        "chapter_pages_wrapped": chapter_pages_wrapped,
        "epilogue_section_pages_wrapped": epilogue_pages_wrapped,
        "subchapter_blocks_wrapped": subchapter_blocks_wrapped,
    }


def _duplicate_items(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def _validate_chapter_pages(text: str) -> tuple[list[str], int, int]:
    lines = text.splitlines()
    errors: list[str] = []
    page_count = 0
    body_text_inside = 0
    index = 0
    while index < len(lines):
        if not CHAPTER_PAGE_OPEN_RE.match(lines[index].strip()):
            index += 1
            continue
        page_count += 1
        close = index + 1
        content = []
        while close < len(lines) and not DIV_CLOSE_RE.match(lines[close].strip()):
            if lines[close].strip():
                content.append(lines[close].strip())
            close += 1
        if close >= len(lines):
            errors.append(f"chapter-page at line {index + 1} is not closed")
        headings = [line for line in content if CHAPTER_HEADING_RE.match(line)]
        body = [line for line in content if not CHAPTER_HEADING_RE.match(line)]
        if len(headings) != 1:
            errors.append(f"chapter-page at line {index + 1} does not contain exactly one chapter heading")
        if body:
            body_text_inside += len(body)
            errors.append(f"chapter-page at line {index + 1} contains body text")
        index = close + 1
    return errors, page_count, body_text_inside


def _validate_epilogue_pages(text: str) -> tuple[list[str], int, int]:
    lines = text.splitlines()
    errors: list[str] = []
    page_count = 0
    body_text_inside = 0
    index = 0
    while index < len(lines):
        if not EPILOGUE_PAGE_OPEN_RE.match(lines[index].strip()):
            index += 1
            continue
        page_count += 1
        close = index + 1
        content = []
        while close < len(lines) and not DIV_CLOSE_RE.match(lines[close].strip()):
            if lines[close].strip():
                content.append(lines[close].strip())
            close += 1
        if close >= len(lines):
            errors.append(f"epilogue-section-page at line {index + 1} is not closed")
        headings = [line for line in content if re.match(r"^##\s+.+", line)]
        body = [line for line in content if not re.match(r"^##\s+.+", line)]
        if len(headings) != 1:
            errors.append(f"epilogue-section-page at line {index + 1} does not contain exactly one heading")
        if body:
            body_text_inside += len(body)
            errors.append(f"epilogue-section-page at line {index + 1} contains body text")
        index = close + 1
    return errors, page_count, body_text_inside


def _validate_subchapter_blocks(text: str) -> tuple[list[str], list[dict[str, Any]], int]:
    lines = text.splitlines()
    errors: list[str] = []
    orphan_markers: list[dict[str, Any]] = []
    block_count = 0
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if SUBCHAPTER_MARKER_RE.match(stripped):
            orphan_markers.append({"line": index + 1, "text": stripped})
        if not SUBCHAPTER_OPEN_RE.match(stripped):
            index += 1
            continue
        block_count += 1
        close = index + 1
        content = []
        while close < len(lines) and not DIV_CLOSE_RE.match(lines[close].strip()):
            if lines[close].strip():
                content.append(lines[close].strip())
            close += 1
        marker_count = sum(1 for line in content if SUBCHAPTER_MARKER_RE.match(line))
        aphorism_count = sum(1 for line in content if APHORISM_INLINE_RE.search(line))
        if marker_count != 1 or aphorism_count != 1 or len(content) != 2:
            errors.append(f"subchapter-block at line {index + 1} must contain only marker and first aphorism")
        index = close + 1
    return errors, orphan_markers, block_count


def _plain_body_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped in {
            '<div class="chapter-page">',
            '<div class="epilogue-section-page">',
            '<div class="subchapter-block">',
            "</div>",
        }:
            continue
        if CHAPTER_HEADING_RE.match(stripped) or SUBCHAPTER_MARKER_RE.match(stripped) or APHORISM_MARKER_RE.match(stripped):
            continue
        cleaned = re.sub(r'<span class="aphorism-inline-number">\d+\.</span>\s*', "", stripped)
        cleaned = re.sub(r"^<p>(.*)</p>$", r"\1", cleaned)
        lines.append(cleaned)
    return lines


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


def _validate(output: str, input_body: str, glossary: str, original_glossary: str) -> dict[str, Any]:
    chapter_errors, chapter_page_count, chapter_body_count = _validate_chapter_pages(output)
    epilogue_errors, epilogue_page_count, epilogue_body_count = _validate_epilogue_pages(output)
    subchapter_errors, orphan_markers, subchapter_block_count = _validate_subchapter_blocks(output)
    duplicate_ids, broken_hrefs = _validate_links(output)
    toc_polluting = [line for line in output.splitlines() if SOURCE_INTERNAL_HEADING_RE.match(line.strip())]
    residues = _endnote_residues(output)
    input_plain = _plain_body_lines(input_body)
    output_plain = _plain_body_lines(output.split("\n# Glossary\n", 1)[0])
    body_preserved = all(line in output_plain for line in input_plain)
    glossary_preserved = glossary == original_glossary
    passed = (
        chapter_page_count == 12
        and not chapter_errors
        and not epilogue_errors
        and not subchapter_errors
        and not orphan_markers
        and not duplicate_ids
        and not broken_hrefs
        and not toc_polluting
        and not residues
        and glossary_preserved
        and body_preserved
    )
    return {
        "passed": passed,
        "chapter_page_count": chapter_page_count,
        "epilogue_page_count": epilogue_page_count,
        "subchapter_block_count": subchapter_block_count,
        "chapter_page_errors": chapter_errors,
        "epilogue_page_errors": epilogue_errors,
        "subchapter_block_errors": subchapter_errors,
        "orphan_subchapter_markers": orphan_markers,
        "toc_polluting_headings": toc_polluting,
        "duplicate_html_ids": duplicate_ids,
        "broken_hrefs": broken_hrefs,
        "endnotes_remaining": bool(residues),
        "endnote_residues": residues,
        "chapter_pages_with_body_text": chapter_body_count,
        "epilogue_pages_with_body_text": epilogue_body_count,
        "glossary_preserved": glossary_preserved,
        "body_prose_preserved": body_preserved,
    }


def apply_v11_pagination_rules(
    text: str,
    *,
    preserve_glossary: bool = True,
    wrap_epilogue_sections: bool = True,
    wrap_subchapter_first_paragraph: bool = True,
) -> tuple[str, dict[str, Any]]:
    body, glossary = _split_glossary(text, preserve_glossary=preserve_glossary)
    transformed_body, transform_report = _transform_body(
        body,
        wrap_epilogue_sections=wrap_epilogue_sections,
        wrap_subchapter_first_paragraph=wrap_subchapter_first_paragraph,
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
            "chapter_pages_wrapped": transform_report["chapter_pages_wrapped"],
            "epilogue_section_pages_wrapped": transform_report["epilogue_section_pages_wrapped"],
            "subchapter_blocks_wrapped": transform_report["subchapter_blocks_wrapped"],
            "chapter_pages_with_body_text": validation["chapter_pages_with_body_text"],
            "epilogue_pages_with_body_text": validation["epilogue_pages_with_body_text"],
            "orphan_subchapter_markers": len(validation["orphan_subchapter_markers"]),
            "toc_polluting_headings": len(validation["toc_polluting_headings"]),
            "glossary_preserved": validation["glossary_preserved"],
            "glossary_links_valid": not validation["broken_hrefs"],
            "endnotes_remaining": validation["endnotes_remaining"],
            "body_prose_preserved": validation["body_prose_preserved"],
        },
        "css": REQUIRED_CSS,
        "validation": validation,
    }
    return output, report


def _main() -> int:
    parser = argparse.ArgumentParser(description="Apply Seneca v11 pagination rules.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--no-preserve-glossary", action="store_true")
    parser.add_argument("--no-wrap-epilogue-sections", action="store_true")
    parser.add_argument("--no-wrap-subchapter-first-paragraph", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report)
    output, report = apply_v11_pagination_rules(
        input_path.read_text(encoding="utf-8"),
        preserve_glossary=not args.no_preserve_glossary,
        wrap_epilogue_sections=not args.no_wrap_epilogue_sections,
        wrap_subchapter_first_paragraph=not args.no_wrap_subchapter_first_paragraph,
    )
    report = {"input": str(input_path), "output": str(output_path), **report}
    output_path.write_text(output, encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
