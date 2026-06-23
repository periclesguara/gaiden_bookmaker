from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any


GLOSSARY_ENTRY_RE = re.compile(r"^\s*\*\*(?P<display>.+?)\*\*\s+-\s+(?P<body>.+?)\s*$")
CATEGORY_RE = re.compile(r"\s*Category:\s*(?P<category>[^.]+)\.\s*$", re.IGNORECASE)
CHAPTER_HEADING_RE = re.compile(r"^Chapter\s+(\d+)$")
MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+.+")
ENDNOTES_HEADING_RE = re.compile(r"^#?\s*(?:Endnotes|Notes|Translator.*Notes)\s*$", re.IGNORECASE)
HTML_TAG_RE = re.compile(r"<[^>]+>")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")
INSERTED_MARKER_RE = re.compile(r"<sup id=\"ref-g\d{3}\"><a href=\"#g\d{3}\">G\d{3}</a></sup>")
ID_RE = re.compile(r"\bid=\"([^\"]+)\"")
HREF_RE = re.compile(r"\bhref=\"#([^\"]+)\"")


def parse_glossary_md(glossary_text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for raw in glossary_text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.upper() == "# GLOSSARY":
            continue
        match = GLOSSARY_ENTRY_RE.match(stripped)
        if not match:
            continue

        display = match.group("display").strip()
        body = match.group("body").strip()
        category = ""
        category_match = CATEGORY_RE.search(body)
        if category_match:
            category = category_match.group("category").strip()
            body = CATEGORY_RE.sub("", body).strip()

        primary_term = display
        aliases: list[str] = []
        alias_match = re.match(r"^(?P<primary>.+?)\s*\((?P<alias>.+)\)\s*$", display)
        if alias_match:
            primary_term = alias_match.group("primary").strip()
            aliases = [part.strip() for part in alias_match.group("alias").split(";") if part.strip()]

        glossary_id = f"G{len(entries) + 1:03d}"
        entries.append(
            {
                "glossary_id": glossary_id,
                "display": display,
                "primary_term": primary_term,
                "aliases": aliases,
                "definition": body,
                "category": category,
                "body_ref_id": f"ref-{glossary_id.lower()}",
                "glossary_anchor_id": glossary_id.lower(),
                "index": len(entries),
            }
        )
    return entries


def _term_candidates(entry: dict[str, Any]) -> list[str]:
    candidates = [str(entry["primary_term"])]
    candidates.extend(str(alias) for alias in entry.get("aliases", []))
    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        cleaned = candidate.strip()
        if not cleaned or cleaned.casefold() in seen:
            continue
        seen.add(cleaned.casefold())
        unique.append(cleaned)
    return unique


def _max_candidate_length(entry: dict[str, Any]) -> int:
    candidates = _term_candidates(entry)
    if not candidates:
        return 0
    return max(len(candidate) for candidate in candidates)


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    return bool(CHAPTER_HEADING_RE.match(stripped) or MARKDOWN_HEADING_RE.match(stripped))


def _protected_ranges(line: str, extra_ranges: list[tuple[int, int]] | None = None) -> list[tuple[int, int]]:
    ranges = [(match.start(), match.end()) for match in HTML_TAG_RE.finditer(line)]
    ranges.extend((match.start(), match.end()) for match in MARKDOWN_LINK_RE.finditer(line))
    if extra_ranges:
        ranges.extend(extra_ranges)
    return ranges


def _overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start < range_end and end > range_start for range_start, range_end in ranges)


def _find_term_match(
    line: str,
    term: str,
    protected_ranges: list[tuple[int, int]],
) -> re.Match[str] | None:
    pattern = re.compile(rf"(?<![\w])({re.escape(term)})(?![\w])")
    for match in pattern.finditer(line):
        if not _overlaps(match.start(), match.end(), protected_ranges):
            return match
    return None


def _line_chapters(lines: list[str]) -> list[int | None]:
    chapters: list[int | None] = []
    current: int | None = None
    for line in lines:
        match = CHAPTER_HEADING_RE.match(line.strip())
        if match:
            current = int(match.group(1))
        chapters.append(current)
    return chapters


def _protected_line_indexes(lines: list[str], *, include_endnotes: bool) -> set[int]:
    protected: set[int] = set()
    in_code = False
    in_frontmatter = False
    frontmatter_done = False
    in_endnotes = False

    for idx, line in enumerate(lines):
        stripped = line.strip()

        if idx == 0 and stripped == "---":
            in_frontmatter = True
            protected.add(idx)
            continue
        if in_frontmatter:
            protected.add(idx)
            if stripped == "---":
                in_frontmatter = False
                frontmatter_done = True
            continue
        if not frontmatter_done and idx > 0:
            frontmatter_done = True

        if stripped.startswith("```"):
            protected.add(idx)
            in_code = not in_code
            continue
        if in_code:
            protected.add(idx)
            continue

        if not include_endnotes and ENDNOTES_HEADING_RE.match(stripped):
            in_endnotes = True
        if in_endnotes:
            protected.add(idx)
            continue

        if _is_heading(line):
            protected.add(idx)

    return protected


def _insert_marker(line: str, start: int, end: int, entry: dict[str, Any]) -> str:
    marker = (
        f'<sup id="{entry["body_ref_id"]}">'
        f'<a href="#{entry["glossary_anchor_id"]}">{entry["glossary_id"]}</a>'
        "</sup>"
    )
    return f"{line[:end]}{marker}{line[end:]}"


def integrate_glossary_into_body(
    body_text: str,
    glossary_entries: list[dict[str, Any]],
    *,
    first_occurrence_only: bool = True,
    include_categories_in_visible_glossary: bool = False,
    include_endnotes: bool = False,
    marker_prefix: str = "G",
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    if marker_prefix != "G":
        raise ValueError("Only marker_prefix='G' is currently supported.")

    lines = body_text.splitlines(keepends=False)
    newline_at_end = body_text.endswith("\n")
    protected_lines = _protected_line_indexes(lines, include_endnotes=include_endnotes)
    chapters = _line_chapters(lines)
    protected_spans: dict[int, list[tuple[int, int]]] = {}
    linked: dict[int, dict[str, Any]] = {}
    unlinked: dict[int, dict[str, Any]] = {}

    process_order = sorted(
        glossary_entries,
        key=lambda entry: (-_max_candidate_length(entry), int(entry.get("index", 0))),
    )

    for entry in process_order:
        entry_index = int(entry.get("index", 0))
        if first_occurrence_only and entry_index in linked:
            continue

        found: tuple[int, re.Match[str], str] | None = None
        for candidate in _term_candidates(entry):
            for line_index, line in enumerate(lines):
                if line_index in protected_lines:
                    continue
                ranges = _protected_ranges(line, protected_spans.get(line_index, []))
                match = _find_term_match(line, candidate, ranges)
                if match:
                    found = (line_index, match, candidate)
                    break
            if found:
                break

        if not found:
            unlinked[entry_index] = {
                "glossary_id": entry["glossary_id"],
                "term": entry["primary_term"],
                "display": entry["display"],
                "category": entry.get("category", ""),
                "reason": "term_not_found_in_body",
            }
            continue

        line_index, match, _candidate = found
        line = lines[line_index]
        start, end = match.start(), match.end()
        matched_text = match.group(0)
        new_line = _insert_marker(line, start, end, entry)
        inserted_len = len(new_line) - len(line)
        lines[line_index] = new_line

        spans = []
        for range_start, range_end in protected_spans.get(line_index, []):
            if range_start >= end:
                spans.append((range_start + inserted_len, range_end + inserted_len))
            else:
                spans.append((range_start, range_end))
        spans.append((start, end))
        protected_spans[line_index] = spans

        linked_record = {
            "glossary_id": entry["glossary_id"],
            "term": entry["primary_term"],
            "display": entry["display"],
            "category": entry.get("category", ""),
            "chapter": chapters[line_index],
            "line": line_index + 1,
            "matched_text": matched_text,
            "body_ref_id": entry["body_ref_id"],
            "glossary_anchor_id": entry["glossary_anchor_id"],
        }
        linked[entry_index] = linked_record

    integrated = "\n".join(lines)
    if newline_at_end:
        integrated += "\n"

    linked_entries = [linked[int(entry.get("index", 0))] for entry in glossary_entries if int(entry.get("index", 0)) in linked]
    unlinked_entries = [
        unlinked[int(entry.get("index", 0))]
        for entry in glossary_entries
        if int(entry.get("index", 0)) in unlinked
    ]
    report = {
        "summary": {
            "glossary_entries_parsed": len(glossary_entries),
            "entries_linked_in_body": len(linked_entries),
            "entries_not_found_in_body": len(unlinked_entries),
        },
        "linked_entries": linked_entries,
        "unlinked_entries": unlinked_entries,
        "options": {
            "first_occurrence_only": first_occurrence_only,
            "include_categories_in_visible_glossary": include_categories_in_visible_glossary,
            "include_endnotes": include_endnotes,
            "marker_prefix": marker_prefix,
        },
    }
    return integrated, linked_entries, report


def build_glossary_section(
    glossary_entries: list[dict[str, Any]],
    linked_entries: list[dict[str, Any]],
    *,
    title: str = "Glossary",
    include_categories: bool = False,
) -> str:
    linked_by_id = {entry["glossary_id"]: entry for entry in linked_entries}
    lines = [f"# {title}", ""]
    for entry in glossary_entries:
        glossary_id = entry["glossary_id"]
        anchor_id = entry["glossary_anchor_id"]
        display = html.escape(str(entry["display"]), quote=False)
        definition = html.escape(str(entry["definition"]), quote=False)
        if include_categories and entry.get("category"):
            definition = f"{definition} Category: {html.escape(str(entry['category']), quote=False)}."
        if glossary_id in linked_by_id:
            ref_id = linked_by_id[glossary_id]["body_ref_id"]
            lines.append(
                f'<p id="{anchor_id}"><strong>{glossary_id} — {display}:</strong> '
                f'{definition} <a href="#{ref_id}">↩</a></p>'
            )
        else:
            lines.append(
                f'<p id="{anchor_id}"><strong>{glossary_id} — {display}:</strong> '
                f"{definition}</p>"
            )
    return "\n".join(lines).strip() + "\n"


def _strip_inserted_markers(text: str) -> str:
    return INSERTED_MARKER_RE.sub("", text)


def _chapter_numbers(text: str) -> list[int]:
    return [
        int(match.group(1))
        for line in text.splitlines()
        if (match := CHAPTER_HEADING_RE.match(line.strip()))
    ]


def _validate_final_file(
    original_body: str,
    integrated_body: str,
    final_text: str,
    glossary_section: str,
    linked_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    original_chapters = _chapter_numbers(original_body)
    final_body_without_markers = _strip_inserted_markers(integrated_body)
    final_chapters = _chapter_numbers(final_body_without_markers)
    ids = ID_RE.findall(final_text)
    hrefs = HREF_RE.findall(final_text)
    duplicate_ids = sorted({value for value in ids if ids.count(value) > 1})
    id_set = set(ids)
    broken_hrefs = sorted({href for href in hrefs if href not in id_set})
    glossary_ids = [entry["glossary_id"] for entry in linked_entries]
    duplicate_glossary_ids = sorted({value for value in glossary_ids if glossary_ids.count(value) > 1})

    markers_inside_headings = 0
    markers_inside_endnotes = 0
    in_endnotes = False
    for line in final_text.splitlines():
        stripped = line.strip()
        if ENDNOTES_HEADING_RE.match(stripped):
            in_endnotes = True
        if INSERTED_MARKER_RE.search(line):
            if _is_heading(line):
                markers_inside_headings += 1
            if in_endnotes:
                markers_inside_endnotes += 1

    expected_sequence = list(range(1, len(original_chapters) + 1))
    chapter_sequence_ok = bool(original_chapters) and original_chapters == expected_sequence
    body_preserved_except_markers = final_body_without_markers == original_body
    validation = {
        "chapter_headings_unchanged": original_chapters == final_chapters,
        "chapter_sequence_ok": chapter_sequence_ok,
        "glossary_heading_count": len(re.findall(r"(?m)^# Glossary$", final_text)),
        "duplicate_html_ids": duplicate_ids,
        "duplicate_glossary_ids": duplicate_glossary_ids,
        "broken_hrefs": broken_hrefs,
        "markers_inside_headings": markers_inside_headings,
        "markers_inside_endnotes": markers_inside_endnotes,
        "category_visible_in_glossary": "Category:" in glossary_section,
        "body_preserved_except_markers": body_preserved_except_markers,
    }
    validation["passed"] = (
        validation["chapter_headings_unchanged"]
        and validation["chapter_sequence_ok"]
        and validation["glossary_heading_count"] == 1
        and not validation["duplicate_html_ids"]
        and not validation["duplicate_glossary_ids"]
        and not validation["broken_hrefs"]
        and validation["markers_inside_headings"] == 0
        and validation["markers_inside_endnotes"] == 0
        and not validation["category_visible_in_glossary"]
        and validation["body_preserved_except_markers"]
    )
    return validation


def build_final_file(
    body_text: str,
    glossary_text: str,
) -> tuple[str, dict[str, Any]]:
    glossary_entries = parse_glossary_md(glossary_text)
    integrated_body, linked_entries, integration_report = integrate_glossary_into_body(
        body_text,
        glossary_entries,
    )
    glossary_section = build_glossary_section(glossary_entries, linked_entries)
    final_text = integrated_body.rstrip() + "\n\n" + glossary_section
    validation = _validate_final_file(
        body_text,
        integrated_body,
        final_text,
        glossary_section,
        linked_entries,
    )
    unlinked_entries = integration_report["unlinked_entries"]
    report = {
        "summary": {
            "glossary_entries_parsed": len(glossary_entries),
            "entries_linked_in_body": len(linked_entries),
            "entries_not_found_in_body": len(unlinked_entries),
            "final_glossary_entries": len(glossary_entries),
            "duplicate_ids": len(validation["duplicate_html_ids"]),
            "broken_links": len(validation["broken_hrefs"]),
            "validation_passed": validation["passed"],
        },
        "linked_entries": linked_entries,
        "unlinked_entries": unlinked_entries,
        "validation": validation,
    }
    return final_text, report


def _main() -> int:
    parser = argparse.ArgumentParser(description="Integrate a Markdown glossary into a body file.")
    parser.add_argument("--body", required=True)
    parser.add_argument("--glossary", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    body_path = Path(args.body)
    glossary_path = Path(args.glossary)
    output_path = Path(args.output)
    report_path = Path(args.report)

    final_text, report = build_final_file(
        body_path.read_text(encoding="utf-8"),
        glossary_path.read_text(encoding="utf-8"),
    )
    report = {
        "input_body": str(body_path),
        "input_glossary": str(glossary_path),
        "output": str(output_path),
        **report,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(final_text, encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
