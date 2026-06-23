from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REPLACEMENTS = {
    "splendour": "splendor",
    "Splendour": "Splendor",
    "behaviour": "behavior",
    "Behaviour": "Behavior",
    "valour": "valor",
    "Valour": "Valor",
    "saviour": "savior",
    "Saviour": "Savior",
}

GLOSSARY_HEADING = "# Glossary"
HTML_TAG_RE = re.compile(r"<[^>]+>")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")
MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+.+")
CHAPTER_HEADING_RE = re.compile(r"^Chapter\s+(\d+)$")
ID_RE = re.compile(r"\bid=\"([^\"]+)\"")
HREF_RE = re.compile(r"\bhref=\"#([^\"]+)\"")


def _split_at_glossary(text: str) -> tuple[str, str, bool]:
    lines = text.splitlines(keepends=True)
    offset = 0
    for line in lines:
        if line.rstrip("\r\n") == GLOSSARY_HEADING:
            return text[:offset], text[offset:], True
        offset += len(line)
    return text, "", False


def _protected_ranges(line: str) -> list[tuple[int, int]]:
    ranges = [(match.start(), match.end()) for match in HTML_TAG_RE.finditer(line)]
    ranges.extend((match.start(), match.end()) for match in MARKDOWN_LINK_RE.finditer(line))
    return ranges


def _overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start < range_end and end > range_start for range_start, range_end in ranges)


def _replace_outside_protected(line: str, counts: dict[str, int]) -> str:
    ranges = _protected_ranges(line)
    pattern = re.compile(r"\b(" + "|".join(re.escape(key) for key in REPLACEMENTS) + r")\b")

    def repl(match: re.Match[str]) -> str:
        start, end = match.span()
        original = match.group(1)
        if _overlaps(start, end, ranges):
            return original
        counts[original] += 1
        return REPLACEMENTS[original]

    return pattern.sub(repl, line)


def _apply_body_replacements(body_part: str) -> tuple[str, dict[str, int]]:
    counts = {key: 0 for key in REPLACEMENTS}
    lines = body_part.splitlines(keepends=True)
    output: list[str] = []
    in_code = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            output.append(line)
            in_code = not in_code
            continue
        if in_code or MARKDOWN_HEADING_RE.match(stripped):
            output.append(line)
            continue
        output.append(_replace_outside_protected(line, counts))

    return "".join(output), counts


def _chapter_headings(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if CHAPTER_HEADING_RE.match(line.strip())]


def _changed_only_by_allowed_replacements(original: str, corrected: str) -> bool:
    expected, _counts = _apply_body_replacements(original)
    return expected == corrected


def _duplicate_items(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def _validate_links(text: str) -> tuple[list[str], list[str], list[str]]:
    ids = ID_RE.findall(text)
    hrefs = HREF_RE.findall(text)
    duplicate_ids = _duplicate_items(ids)
    id_set = set(ids)
    broken_hrefs = sorted({href for href in hrefs if href not in id_set})
    return ids, duplicate_ids, broken_hrefs


def apply_body_only_corrections(text: str) -> tuple[str, dict[str, Any]]:
    body_part, protected_part, glossary_found = _split_at_glossary(text)
    if not glossary_found:
        report = {
            "scope": "body_only_before_glossary",
            "glossary_found": False,
            "protected_part_unchanged": False,
            "replacements": {
                key: {"replacement": value, "count": 0}
                for key, value in REPLACEMENTS.items()
            },
            "summary": {
                "total_replacements": 0,
                "chapter_headings_changed": False,
                "glossary_links_changed": False,
                "duplicate_html_ids": 0,
                "broken_links": 0,
                "validation_passed": False,
            },
            "validation": {
                "error": "glossary_heading_not_found",
                "passed": False,
            },
        }
        return text, report

    corrected_body, counts = _apply_body_replacements(body_part)
    output = corrected_body + protected_part

    original_ids, _original_duplicate_ids, original_broken_links = _validate_links(text)
    output_ids, duplicate_ids, broken_links = _validate_links(output)
    original_hrefs = HREF_RE.findall(text)
    output_hrefs = HREF_RE.findall(output)
    chapter_headings_changed = _chapter_headings(body_part) != _chapter_headings(corrected_body)
    protected_part_unchanged = _split_at_glossary(output)[1] == protected_part
    glossary_links_changed = original_ids != output_ids or original_hrefs != output_hrefs
    only_allowed_changes = _changed_only_by_allowed_replacements(body_part, corrected_body)
    glossary_heading_count = sum(
        1 for line in output.splitlines() if line == GLOSSARY_HEADING
    )

    replacements = {
        key: {"replacement": value, "count": counts[key]}
        for key, value in REPLACEMENTS.items()
    }
    total_replacements = sum(counts.values())
    validation = {
        "glossary_heading_count": glossary_heading_count,
        "protected_part_unchanged": protected_part_unchanged,
        "chapter_headings_changed": chapter_headings_changed,
        "glossary_links_changed": glossary_links_changed,
        "html_ids_changed": original_ids != output_ids,
        "duplicate_html_ids": duplicate_ids,
        "broken_links": broken_links,
        "original_broken_links": original_broken_links,
        "replacement_after_glossary": protected_part != _split_at_glossary(output)[1],
        "only_approved_body_changes": only_allowed_changes,
    }
    validation["passed"] = (
        glossary_found
        and glossary_heading_count == 1
        and protected_part_unchanged
        and not chapter_headings_changed
        and not glossary_links_changed
        and not duplicate_ids
        and not broken_links
        and only_allowed_changes
    )

    report = {
        "scope": "body_only_before_glossary",
        "glossary_found": True,
        "protected_part_unchanged": protected_part_unchanged,
        "replacements": replacements,
        "summary": {
            "total_replacements": total_replacements,
            "chapter_headings_changed": chapter_headings_changed,
            "glossary_links_changed": glossary_links_changed,
            "duplicate_html_ids": len(duplicate_ids),
            "broken_links": len(broken_links),
            "validation_passed": validation["passed"],
        },
        "validation": validation,
    }
    return output, report


def _main() -> int:
    parser = argparse.ArgumentParser(description="Apply final body-only spelling corrections.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report)

    text = input_path.read_text(encoding="utf-8")
    output, report = apply_body_only_corrections(text)
    report = {
        "input": str(input_path),
        "output": str(output_path),
        **report,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not report["glossary_found"]:
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output, encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
