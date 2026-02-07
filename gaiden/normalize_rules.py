from __future__ import annotations

import re
from typing import Iterable

START_MARKERS = [
    r"\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG EBOOK",
    r"\*\*\*START OF (THE|THIS) PROJECT GUTENBERG EBOOK",
    r"START OF (THE|THIS) PROJECT GUTENBERG EBOOK",
    r"START OF PROJECT GUTENBERG EBOOK",
    r"\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG",
    r"\*\*\*START OF (THE|THIS) PROJECT GUTENBERG",
    r"START OF (THE|THIS) PROJECT GUTENBERG",
    r"START OF PROJECT GUTENBERG",
]

END_MARKERS = [
    r"\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG EBOOK",
    r"\*\*\*END OF (THE|THIS) PROJECT GUTENBERG EBOOK",
    r"END OF (THE|THIS) PROJECT GUTENBERG EBOOK",
    r"END OF PROJECT GUTENBERG EBOOK",
    r"\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG",
    r"\*\*\*END OF (THE|THIS) PROJECT GUTENBERG",
    r"END OF (THE|THIS) PROJECT GUTENBERG",
    r"END OF PROJECT GUTENBERG",
]

LICENSE_START_MARKERS = [
    r"START:\s*FULL LICENSE",
    r"FULL PROJECT GUTENBERG LICENSE",
    r"PROJECT GUTENBERG LICENSE",
]

LICENSE_TAIL_MARKERS = [
    "project gutenberg",
    "gutenberg license",
    "www.gutenberg.org",
    "this ebook is for the use of anyone anywhere",
    "start: full license",
    "end: full license",
    "full project gutenberg license",
    "produced by",
    "this file was produced by",
]

FRONTMATTER_KEYWORDS = [
    "frontispiece",
    "copyright",
    "about this edition",
]

META_PREFIXES = (
    "title:",
    "author:",
    "release date:",
    "most recently updated:",
    "language:",
    "ebook #",
)


def _find_any_marker(lines: list[str], patterns: Iterable[str], start_at: int = 0) -> int | None:
    rx_list = [re.compile(p, re.IGNORECASE) for p in patterns]
    for i in range(start_at, len(lines)):
        line = lines[i]
        for rx in rx_list:
            if rx.search(line):
                return i
    return None


def _collapse_blank_max(lines: list[str], max_blank: int = 2) -> list[str]:
    out: list[str] = []
    blank_count = 0
    for line in lines:
        if line.strip() == "":
            blank_count += 1
            if blank_count > max_blank:
                continue
        else:
            blank_count = 0
        out.append(line)
    return out


def _is_heading_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if re.match(r"^(\d+)\s*-\s+.+$", s):
        return True
    if re.match(r"^(CHAPTER|ADVENTURE|PART|BOOK|CAP[ÍI]TULO|CAPITOLO|CHAPITRE|TEIL|KAPITEL)\b", s, flags=re.IGNORECASE):
        return True
    if re.match(r"^#+\s+.+$", s):
        return True
    if re.match(r"^\d+\s*[\.\-]\s+.+$", s):
        return True
    if re.match(r"^[IVXLCDM]+\.\s+.+$", s, flags=re.IGNORECASE):
        return True
    return False


def strip_gutenberg_boilerplate(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()

    start_idx = _find_any_marker(lines, START_MARKERS)
    end_idx = _find_any_marker(lines, END_MARKERS, start_at=(start_idx + 1) if start_idx is not None else 0)

    if start_idx is not None:
        lines = lines[start_idx + 1 :]
        if end_idx is not None and end_idx > start_idx:
            lines = lines[: end_idx - start_idx - 1]
    elif end_idx is not None:
        lines = lines[:end_idx]

    # remove tail license block (best-effort)
    lic_idx = _find_any_marker(lines, LICENSE_START_MARKERS)
    if lic_idx is not None and lic_idx > 0:
        lines = lines[:lic_idx]
    else:
        tail_window = 400
        start = max(0, len(lines) - tail_window)
        marker_idx: int | None = None
        for i in range(start, len(lines)):
            low = lines[i].lower()
            if any(marker in low for marker in LICENSE_TAIL_MARKERS):
                marker_idx = i if marker_idx is None else min(marker_idx, i)
        if marker_idx is not None:
            lines = lines[:marker_idx]

    return "\n".join(lines).strip()


def normalize_to_headings_only(text: str) -> str:
    cleaned = strip_gutenberg_boilerplate(text)
    lines = cleaned.splitlines()

    # remove top metadata lines (Gutenberg header leftovers)
    trimmed: list[str] = []
    for idx, line in enumerate(lines):
        low = line.strip().lower()
        if idx < 80 and low.startswith(META_PREFIXES):
            continue
        trimmed.append(line)
    lines = trimmed

    # strip legacy frontmatter blocks before first heading
    head_limit = min(len(lines), 200)
    head = lines[:head_limit]
    tail = lines[head_limit:]
    first_heading_idx = None
    for i, line in enumerate(head):
        if _is_heading_line(line):
            first_heading_idx = i
            break
    if first_heading_idx is None:
        first_heading_idx = head_limit

    kept: list[str] = []
    i = 0
    while i < first_heading_idx:
        if head[i].strip() == "":
            kept.append(head[i])
            i += 1
            continue
        j = i
        block: list[str] = []
        while j < first_heading_idx and head[j].strip() != "":
            block.append(head[j])
            j += 1
        block_text = "\n".join(block).lower()
        if any(keyword in block_text for keyword in FRONTMATTER_KEYWORDS):
            pass
        else:
            kept.extend(block)
        i = j

    lines = kept + head[first_heading_idx:] + tail
    lines = _collapse_blank_max(lines, max_blank=2)
    return "\n".join(lines).strip()


def compute_normalize_report(raw: str, normalized: str) -> dict:
    raw_low = raw.lower()
    markers: list[str] = []
    if re.search("|".join(START_MARKERS), raw, flags=re.IGNORECASE):
        markers.append("START_GUTENBERG")
    if re.search("|".join(END_MARKERS), raw, flags=re.IGNORECASE):
        markers.append("END_GUTENBERG")
    if re.search("|".join(LICENSE_START_MARKERS), raw, flags=re.IGNORECASE):
        markers.append("LICENSE_START")
    if any(m in raw_low for m in LICENSE_TAIL_MARKERS):
        markers.append("LICENSE_TAIL")

    head_sample = "\n".join(raw.splitlines()[:200]).lower()
    removed_frontmatter_legacy = any(keyword in head_sample for keyword in FRONTMATTER_KEYWORDS)
    removed_license_blocks = bool(re.search("|".join(LICENSE_START_MARKERS), raw, flags=re.IGNORECASE)) or any(
        m in raw_low for m in LICENSE_TAIL_MARKERS
    )

    raw_chars = len(raw)
    normalized_chars = len(normalized)
    delta_ratio = (normalized_chars / raw_chars) if raw_chars else 0.0

    return {
        "removed_markers_found": markers,
        "removed_license_blocks": removed_license_blocks,
        "removed_frontmatter_legacy": removed_frontmatter_legacy,
        "normalized_char_count": normalized_chars,
        "raw_char_count": raw_chars,
        "delta_ratio": round(delta_ratio, 4),
    }
