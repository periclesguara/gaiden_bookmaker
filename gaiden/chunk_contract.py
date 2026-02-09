from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

SCHEMA_VERSION = "chunks_manifest_v2"

DEFAULT_TARGET_TOKENS = 1500
DEFAULT_MAX_TOKENS = 2000

ALLOWED_LANG = "en"

HEADING_KEYWORDS = ("CHAPTER", "PART", "BOOK")

KEYWORD_RE = re.compile(
    rf"^({'|'.join(HEADING_KEYWORDS)})\b(?:\s+([IVXLCDM0-9]+))?(?:[\.\-:]?\s*(.*))?$",
    flags=re.IGNORECASE,
)

MARKDOWN_RE = re.compile(r"^#{1,6}\s+(.+)$")
ROMAN_DOT_RE = re.compile(r"^([IVXLCDM]+)\.\s+(.+)$", flags=re.IGNORECASE)
ROMAN_ONLY_RE = re.compile(r"^[IVXLCDM]+$", flags=re.IGNORECASE)


ROMAN_MAP = {
    "I": 1,
    "V": 5,
    "X": 10,
    "L": 50,
    "C": 100,
    "D": 500,
    "M": 1000,
}


def roman_to_int(value: str) -> Optional[int]:
    s = value.upper().strip()
    if not s or not all(c in ROMAN_MAP for c in s):
        return None
    total, prev = 0, 0
    for c in reversed(s):
        val = ROMAN_MAP[c]
        total += -val if val < prev else val
        prev = val
    return total


@dataclass
class HeadingMatch:
    heading_line: str
    heading_number: Optional[int]
    heading_title: str
    consumed_lines: int
    note: str


def _is_blank(line: str) -> bool:
    return line.strip() == ""


def _is_isolated(lines: list[str], start_idx: int, end_idx: int) -> bool:
    before_blank = start_idx == 0 or _is_blank(lines[start_idx - 1])
    after_blank = end_idx >= len(lines) - 1 or _is_blank(lines[end_idx + 1])
    return before_blank or after_blank


def detect_heading(lines: list[str], idx: int) -> Optional[HeadingMatch]:
    line = lines[idx].strip()
    if not line:
        return None

    # Keyword headings are always accepted, isolation not required.
    m = KEYWORD_RE.match(line)
    if m:
        num_raw = (m.group(2) or "").strip()
        title = (m.group(3) or "").strip()
        if num_raw:
            heading_number = int(num_raw) if num_raw.isdigit() else roman_to_int(num_raw)
        else:
            heading_number = None
        return HeadingMatch(
            heading_line=line,
            heading_number=heading_number,
            heading_title=title,
            consumed_lines=1,
            note="keyword",
        )

    # Markdown headings require isolation.
    m = MARKDOWN_RE.match(line)
    if m and _is_isolated(lines, idx, idx):
        return HeadingMatch(
            heading_line=line,
            heading_number=None,
            heading_title=m.group(1).strip(),
            consumed_lines=1,
            note="markdown",
        )

    # Roman numeral headings with dot require isolation.
    m = ROMAN_DOT_RE.match(line)
    if m and _is_isolated(lines, idx, idx):
        return HeadingMatch(
            heading_line=line,
            heading_number=roman_to_int(m.group(1)) or None,
            heading_title=m.group(2).strip(),
            consumed_lines=1,
            note="roman-dot",
        )

    # Roman-only line with title on next line, requires isolation.
    if ROMAN_ONLY_RE.match(line) and idx + 1 < len(lines):
        title = lines[idx + 1].strip()
        if title:
            if _is_isolated(lines, idx, idx + 1):
                return HeadingMatch(
                    heading_line=line,
                    heading_number=roman_to_int(line) or None,
                    heading_title=title,
                    consumed_lines=2,
                    note="roman-only-next-line",
                )

    return None
