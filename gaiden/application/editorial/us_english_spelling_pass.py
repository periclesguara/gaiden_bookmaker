from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


GLOSSARY_HEADING = "# Glossary"
TITLE_HEADING = "# Seneca’s Dialogues"

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

SUMMARY_GROUPS = {
    "splendour_to_splendor": ("splendour", "Splendour"),
    "behaviour_to_behavior": ("behaviour", "Behaviour"),
    "valour_to_valor": ("valour", "Valour"),
    "saviour_to_savior": ("saviour", "Saviour"),
}

HTML_TAG_RE = re.compile(r"<[^>]+>")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")
MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+.+")
CHAPTER_HEADING_RE = re.compile(r"^## Chapter \d{2} — .+$")
SOURCE_HEADING_RE = re.compile(r"^#{2,6}\s+(?:Part|Book|Chapter|Section|Aphorism)\b", re.IGNORECASE)
INTERNAL_MARKER_RE = re.compile(r'^<p class="aphorism-number">\d+</p>$')
INTERNAL_MARKDOWN_RE = re.compile(r"^#{3,6}\s+\d+\s*$")
ID_RE = re.compile(r'\bid="([^"]+)"')
HREF_RE = re.compile(r'\bhref="#([^"]+)"')
ENDNOTES_RE = re.compile(r"^#?\s*Endnotes\b|\bEndnotes\b|↩︎", re.IGNORECASE)

RESIDUE_PATTERNS = [
    "# Endnotes",
    "Endnotes",
    "↩︎",
    "J. E. B. Mayor",
    "Koch declares",
    "Gertz reads",
    "Lipsius",
    "La Grange",
]


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


def _replace_outside_tags(line: str, counts: dict[str, int]) -> str:
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


def _apply_body_replacements(body: str) -> tuple[str, dict[str, int]]:
    counts = {key: 0 for key in REPLACEMENTS}
    output: list[str] = []
    in_code = False

    for line in body.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("```"):
            output.append(line)
            in_code = not in_code
            continue
        if in_code or MARKDOWN_HEADING_RE.match(stripped) or INTERNAL_MARKER_RE.match(stripped):
            output.append(line)
            continue
        output.append(_replace_outside_tags(line, counts))

    return "".join(output), counts


def _duplicate_items(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def _chapter_headings(text: str) -> list[str]:
    return [line for line in text.splitlines() if CHAPTER_HEADING_RE.match(line.strip())]


def _internal_markers(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if INTERNAL_MARKER_RE.match(line.strip())]


def _broken_hrefs(text: str) -> tuple[list[str], list[str]]:
    ids = ID_RE.findall(text)
    hrefs = HREF_RE.findall(text)
    duplicate_ids = _duplicate_items(ids)
    id_set = set(ids)
    return duplicate_ids, sorted({href for href in hrefs if href not in id_set})


def _residues_before_glossary(body: str) -> list[dict[str, Any]]:
    residues: list[dict[str, Any]] = []
    for line_no, line in enumerate(body.splitlines(), start=1):
        for pattern in RESIDUE_PATTERNS:
            if pattern in line:
                residues.append({"line": line_no, "pattern": pattern, "text": line.strip()})
                break
    return residues


def _unauthorized_changed_lines(original_body: str, corrected_body: str) -> list[dict[str, Any]]:
    expected_body, _counts = _apply_body_replacements(original_body)
    if expected_body == corrected_body:
        return []

    original_lines = original_body.splitlines()
    corrected_lines = corrected_body.splitlines()
    max_len = max(len(original_lines), len(corrected_lines))
    changed: list[dict[str, Any]] = []
    for index in range(max_len):
        original = original_lines[index] if index < len(original_lines) else ""
        corrected = corrected_lines[index] if index < len(corrected_lines) else ""
        if original != corrected:
            changed.append({"line": index + 1, "original": original, "corrected": corrected})
    return changed


def _replacement_report(counts: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {"from": "splendour", "to": "splendor", "count": counts["splendour"] + counts["Splendour"]},
        {"from": "behaviour", "to": "behavior", "count": counts["behaviour"] + counts["Behaviour"]},
        {"from": "valour", "to": "valor", "count": counts["valour"] + counts["Valour"]},
        {"from": "saviour", "to": "savior", "count": counts["saviour"] + counts["Saviour"]},
    ]


def _validate(
    original: str,
    output: str,
    *,
    original_body: str,
    corrected_body: str,
    original_protected: str,
    output_protected: str,
    glossary_found: bool,
) -> dict[str, Any]:
    title_count = sum(1 for line in output.splitlines() if line == TITLE_HEADING)
    chapter_headings = _chapter_headings(output)
    glossary_count = sum(1 for line in output.splitlines() if line == GLOSSARY_HEADING)
    source_headings = [
        line
        for line in output.splitlines()
        if SOURCE_HEADING_RE.match(line.strip()) and not CHAPTER_HEADING_RE.match(line.strip())
    ]
    internal_markdown = [line for line in output.splitlines() if INTERNAL_MARKDOWN_RE.match(line.strip())]
    headings_with_markers = [line for line in chapter_headings if "<sup" in line]
    duplicate_ids, broken_hrefs = _broken_hrefs(output)
    residues = _residues_before_glossary(corrected_body)
    unauthorized = _unauthorized_changed_lines(original_body, corrected_body)
    original_chapters = _chapter_headings(original)
    output_chapters = _chapter_headings(output)
    original_markers = _internal_markers(original)
    output_markers = _internal_markers(output)
    changed_after_glossary = original_protected != output_protected
    chapter_structure_preserved = original_chapters == output_chapters and len(output_chapters) == 12
    internal_markers_preserved = original_markers == output_markers
    passed = (
        glossary_found
        and title_count == 1
        and len(chapter_headings) == 12
        and glossary_count == 1
        and not source_headings
        and not internal_markdown
        and not headings_with_markers
        and not duplicate_ids
        and not broken_hrefs
        and not changed_after_glossary
        and not unauthorized
        and not residues
        and chapter_structure_preserved
        and internal_markers_preserved
    )
    return {
        "passed": passed,
        "title_count": title_count,
        "chapter_count": len(chapter_headings),
        "glossary_count": glossary_count,
        "source_headings_remaining": source_headings,
        "internal_markdown_headings_remaining": internal_markdown,
        "duplicate_html_ids": duplicate_ids,
        "broken_hrefs": broken_hrefs,
        "changed_after_glossary": changed_after_glossary,
        "glossary_byte_for_byte_unchanged": not changed_after_glossary,
        "unauthorized_changed_lines": unauthorized,
        "headings_with_glossary_markers": headings_with_markers,
        "chapter_structure_preserved": chapter_structure_preserved,
        "internal_markers_preserved": internal_markers_preserved,
        "possible_residue_before_glossary": residues,
        "endnotes_remaining": bool(residues),
    }


def apply_us_english_spelling_pass(
    text: str,
    *,
    protect_glossary: bool = True,
) -> tuple[str, dict[str, Any]]:
    body_part, protected_part, glossary_found = _split_at_glossary(text)
    corrected_body, counts = _apply_body_replacements(body_part)
    output = corrected_body + (protected_part if protect_glossary else "")
    if not protect_glossary and protected_part:
        unprotected_corrected, unprotected_counts = _apply_body_replacements(protected_part)
        output = corrected_body + unprotected_corrected
        for key, count in unprotected_counts.items():
            counts[key] += count

    output_body, output_protected, _output_glossary_found = _split_at_glossary(output)
    validation = _validate(
        text,
        output,
        original_body=body_part,
        corrected_body=output_body,
        original_protected=protected_part,
        output_protected=output_protected,
        glossary_found=glossary_found,
    )
    total_replacements = sum(counts.values())
    summary_counts = {
        key: sum(counts[source] for source in sources)
        for key, sources in SUMMARY_GROUPS.items()
    }
    report = {
        "summary": {
            "validation_passed": validation["passed"],
            "total_replacements": total_replacements,
            **summary_counts,
            "glossary_preserved": validation["glossary_count"] == 1 and not validation["changed_after_glossary"],
            "glossary_links_valid": not validation["broken_hrefs"],
            "chapter_structure_preserved": validation["chapter_structure_preserved"],
            "internal_markers_preserved": validation["internal_markers_preserved"],
            "endnotes_remaining": validation["endnotes_remaining"],
            "unauthorized_changes": len(validation["unauthorized_changed_lines"]),
        },
        "replacements": _replacement_report(counts),
        "validation": validation,
    }
    return output, report


def _main() -> int:
    parser = argparse.ArgumentParser(description="Apply body-only US English spelling pass.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--no-protect-glossary", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report)

    output, report = apply_us_english_spelling_pass(
        input_path.read_text(encoding="utf-8"),
        protect_glossary=not args.no_protect_glossary,
    )
    report = {"input": str(input_path), "output": str(output_path), **report}
    output_path.write_text(output, encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
