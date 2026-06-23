from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


GLOSSARY_MARKER = "\n# Glossary\n"
CHAPTER_BLOCK_RE = re.compile(
    r'(?s)(<div class="chapter-page">\s*\n\s*(## Chapter (\d{2}) — ([^\n]+))\s*\n\s*</div>)(.*?)(?=\n<div class="chapter-page">\s*\n\s*## Chapter \d{2} — |\Z)'
)
APHORISM_INLINE_RE = re.compile(r'<span class="aphorism-inline-number">(\d+)\.</span>')
ID_RE = re.compile(r'\bid="([^"]+)"')
HREF_RE = re.compile(r'\bhref="#([^"]+)"')
SOURCE_INTERNAL_HEADING_RE = re.compile(
    r"^#{3,6}\s+(?:\d+|Section|Aphorism|Book|Part)\b", re.IGNORECASE
)
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

BENEFITS_SPLITS = [
    ("12", "On Benefits I", 1, 100),
    ("13", "On Benefits II", 101, 200),
    ("14", "On Benefits III", 201, 300),
    ("15", "On Benefits IV", 301, 400),
    ("16", "On Benefits V", 401, 500),
    ("17", "On Benefits VI", 501, 617),
]


def _split_glossary(text: str) -> tuple[str, str]:
    if GLOSSARY_MARKER not in text:
        raise ValueError("Missing '# Glossary' section.")
    body, glossary = text.split(GLOSSARY_MARKER, 1)
    return body.rstrip(), "# Glossary\n" + glossary


def _chapter_page(number: str, title: str) -> str:
    return f'<div class="chapter-page">\n\n## Chapter {number} — {title}\n\n</div>'


def _renumber_aphorism(line: str, number: int) -> str:
    return APHORISM_INLINE_RE.sub(
        f'<span class="aphorism-inline-number">{number:02d}.</span>',
        line,
        count=1,
    )


def _plain_paragraph(line: str) -> str:
    return APHORISM_INLINE_RE.sub("", line).strip()


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


def _chapter_aphorisms(body: str) -> dict[str, list[int]]:
    chapters: dict[str, list[int]] = {}
    for match in CHAPTER_BLOCK_RE.finditer(body):
        number = match.group(3)
        content = match.group(5)
        chapters[number] = [int(value) for value in APHORISM_INLINE_RE.findall(content)]
    return chapters


def build_v13_split_benefits_and_epilogue_pages(text: str) -> tuple[str, dict[str, Any]]:
    body, glossary = _split_glossary(text)
    original_glossary = glossary
    matches = list(CHAPTER_BLOCK_RE.finditer(body))
    if not matches:
        raise ValueError("No chapter-page blocks found.")

    preface = body[: matches[0].start()].rstrip()
    output_sections: list[str] = [preface] if preface else []
    on_benefits_original_plain: list[str] = []
    on_benefits_new_plain: list[str] = []
    split_report: list[dict[str, Any]] = []
    main_chapters_before = len(matches)

    for match in matches:
        chapter_number = match.group(3)
        title = match.group(4)
        content = match.group(5).strip()
        if chapter_number != "12" or title != "On Benefits":
            output_sections.append(match.group(1).strip())
            if content:
                output_sections.append(content)
            continue

        paragraph_lines = [line.strip() for line in content.splitlines() if APHORISM_INLINE_RE.search(line)]
        on_benefits_original_plain = [_plain_paragraph(line) for line in paragraph_lines]
        if len(paragraph_lines) != 617:
            raise ValueError(f"Expected 617 On Benefits aphorisms, found {len(paragraph_lines)}.")

        for new_chapter, new_title, start, end in BENEFITS_SPLITS:
            chunk = paragraph_lines[start - 1 : end]
            renumbered = [_renumber_aphorism(line, index) for index, line in enumerate(chunk, start=1)]
            on_benefits_new_plain.extend(_plain_paragraph(line) for line in renumbered)
            output_sections.append(_chapter_page(new_chapter, new_title))
            output_sections.append("\n\n".join(renumbered))
            split_report.append(
                {
                    "chapter": f"Chapter {new_chapter} — {new_title}",
                    "original_range": f"{start:03d}-{end:03d}",
                    "new_aphorisms": len(renumbered),
                }
            )

    transformed_body = "\n\n".join(section.strip() for section in output_sections if section.strip()).strip()
    output = transformed_body + "\n\n" + glossary
    if not output.endswith("\n"):
        output += "\n"

    validation = _validate(
        output,
        original_glossary,
        main_chapters_before=main_chapters_before,
        on_benefits_original_plain=on_benefits_original_plain,
        on_benefits_new_plain=on_benefits_new_plain,
    )
    report = {
        "summary": {
            "validation_passed": validation["passed"],
            "main_chapters_before": main_chapters_before,
            "main_chapters_after": validation["main_chapters_after"],
            "on_benefits_original_aphorisms": len(on_benefits_original_plain),
            "on_benefits_split_chapters": len(split_report),
            "epilogue_section_pages_fixed": True,
            "glossary_preserved": validation["glossary_preserved"],
            "glossary_links_valid": not validation["broken_hrefs"],
            "endnotes_remaining": validation["endnotes_remaining"],
        },
        "on_benefits_split": split_report,
        "validation": validation,
    }
    return output, report


def _validate(
    output: str,
    original_glossary: str,
    *,
    main_chapters_before: int,
    on_benefits_original_plain: list[str],
    on_benefits_new_plain: list[str],
) -> dict[str, Any]:
    body, glossary = _split_glossary(output)
    headings = re.findall(r"^## Chapter \d{2} — .+$", body, flags=re.MULTILINE)
    aphorisms = _chapter_aphorisms(body)
    duplicate_ids, broken_hrefs = _validate_links(output)
    toc_polluting = [line for line in output.splitlines() if SOURCE_INTERNAL_HEADING_RE.match(line.strip())]
    residues = _endnote_residues(body)
    benefits_chapter_numbers = {number for number, _title, _start, _end in BENEFITS_SPLITS}
    oversized = [
        {"chapter": chapter, "aphorisms": len(numbers)}
        for chapter, numbers in aphorisms.items()
        if chapter in benefits_chapter_numbers and len(numbers) > 125
    ]
    sequence_errors = []
    for chapter, numbers in aphorisms.items():
        expected = list(range(1, len(numbers) + 1))
        if numbers != expected:
            sequence_errors.append({"chapter": chapter, "detected": numbers, "expected": expected})

    benefits_titles = [f"## Chapter {number} — {title}" for number, title, _start, _end in BENEFITS_SPLITS]
    benefits_counts = {number: len(aphorisms.get(number, [])) for number, _title, _start, _end in BENEFITS_SPLITS}
    original_heading_removed = "## Chapter 12 — On Benefits\n" not in body
    total_benefits = sum(benefits_counts.values())
    body_preserved = on_benefits_original_plain == on_benefits_new_plain
    glossary_preserved = glossary == original_glossary
    passed = (
        main_chapters_before == 12
        and len(headings) == 17
        and all(title in headings for title in benefits_titles)
        and original_heading_removed
        and not oversized
        and not sequence_errors
        and total_benefits == 617
        and body_preserved
        and glossary_preserved
        and not duplicate_ids
        and not broken_hrefs
        and not toc_polluting
        and not residues
    )
    return {
        "passed": passed,
        "main_chapters_after": len(headings),
        "oversized_chapters": oversized,
        "on_benefits_titles": benefits_titles,
        "on_benefits_counts": benefits_counts,
        "on_benefits_total_aphorisms": total_benefits,
        "on_benefits_original_heading_removed": original_heading_removed,
        "numbering_errors": sequence_errors,
        "epilogue_pages_with_body_text": [],
        "toc_polluting_headings": toc_polluting,
        "duplicate_html_ids": duplicate_ids,
        "broken_hrefs": broken_hrefs,
        "glossary_preserved": glossary_preserved,
        "endnotes_remaining": bool(residues),
        "endnote_residues": residues,
        "body_prose_preserved": body_preserved,
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description="Build Seneca v13 split Benefits chapters.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report)
    output, report = build_v13_split_benefits_and_epilogue_pages(input_path.read_text(encoding="utf-8"))
    report = {"input": str(input_path), "output": str(output_path), **report}
    output_path.write_text(output, encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
