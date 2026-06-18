from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
CHAPTER_RE = re.compile(r"\bchapter\s+([0-9ivxlcdm]+)\b", re.IGNORECASE)


def inspect_markdown_headings(markdown_text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    headings: list[dict[str, Any]] = []
    chapters: list[dict[str, Any]] = []
    warnings: list[str] = []

    for line_number, line in enumerate(markdown_text.splitlines(), start=1):
        match = HEADING_RE.match(line)
        if not match:
            continue
        marker, title = match.groups()
        heading = {
            "line": line_number,
            "level": len(marker),
            "title": title,
        }
        headings.append(heading)
        chapter_match = CHAPTER_RE.search(title)
        if chapter_match:
            chapters.append(
                {
                    "line": line_number,
                    "level": len(marker),
                    "title": title,
                    "chapter": chapter_match.group(1),
                }
            )

    if not headings:
        warnings.append("No Markdown headings detected.")

    headings_report = {
        "schema": "gaiden.heading_inspection.v1",
        "total_headings": len(headings),
        "headings": headings,
        "warnings": warnings,
    }
    chapters_report = {
        "schema": "gaiden.chapter_candidates.v1",
        "count": len(chapters),
        "chapter_candidates": chapters,
    }
    return headings_report, chapters_report


def write_inspection_reports(
    markdown_text: str,
    headings_report_path: str | Path,
    chapters_candidates_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    headings_report, chapters_report = inspect_markdown_headings(markdown_text)
    headings_path = Path(headings_report_path)
    chapters_path = Path(chapters_candidates_path)
    headings_path.parent.mkdir(parents=True, exist_ok=True)
    chapters_path.parent.mkdir(parents=True, exist_ok=True)
    headings_path.write_text(
        json.dumps(headings_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    chapters_path.write_text(
        json.dumps(chapters_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return headings_report, chapters_report
