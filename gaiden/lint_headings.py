from __future__ import annotations

import re
from dataclasses import dataclass


RE_CHAPTER = re.compile(r"^\s*(CHAPTER|Chapter)\s+([IVXLC]+|\d+)\b", re.IGNORECASE)
RE_SECTION_NUM = re.compile(r"^\s*\d+\s*[\.\-–—:]\s+\S+")
RE_ALLCAPS_LINE = re.compile(r"^[A-Z0-9][A-Z0-9\s'\",;\-–—:]{8,}$")


@dataclass
class LintIssue:
    code: str
    line_no: int
    line: str


def _is_heading_candidate(line: str) -> bool:
    if RE_CHAPTER.match(line):
        return True
    if RE_SECTION_NUM.match(line):
        return True
    if RE_ALLCAPS_LINE.match(line.strip()) and len(line.strip().split()) <= 12:
        return True
    return False


def lint_headings(text: str) -> list[LintIssue]:
    issues: list[LintIssue] = []
    lines = text.splitlines()

    for i, line in enumerate(lines):
        if not _is_heading_candidate(line):
            continue
        prev = lines[i - 1].strip() if i > 0 else ""
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if prev and nxt:
            issues.append(LintIssue("HEADING_NOT_SEPARATED", i + 1, line))

    for i in range(1, len(lines)):
        if lines[i].strip() and lines[i].strip() == lines[i - 1].strip() and _is_heading_candidate(lines[i]):
            issues.append(LintIssue("DUPLICATE_HEADING", i + 1, lines[i]))

    for i, line in enumerate(lines):
        if RE_ALLCAPS_LINE.match(line.strip()) and not RE_CHAPTER.match(line):
            issues.append(LintIssue("SUSPECT_ALLCAPS", i + 1, line))

    return issues
