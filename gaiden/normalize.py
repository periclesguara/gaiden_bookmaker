from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Tuple

NORMALIZED_DIR = Path("data/normalized")

ROMAN_MAP = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

def roman_to_int(s: str) -> int | None:
    s = s.upper().strip()
    if not s or not all(c in ROMAN_MAP for c in s):
        return None
    total, prev = 0, 0
    for c in reversed(s):
        val = ROMAN_MAP[c]
        total += -val if val < prev else val
        prev = val
    return total

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _find_marker(lines: list[str], pattern: str) -> int | None:
    rx = re.compile(pattern, re.IGNORECASE)
    for i, line in enumerate(lines):
        if rx.search(line):
            return i
    return None

def _slice_gutenberg_main(lines: list[str]) -> list[str]:
    """
    Prefer slicing between Gutenberg START/END markers.
    Fallback: remove tail license starting at 'START: FULL LICENSE'.
    """
    # Most common markers:
    start_i = _find_marker(lines, r"\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG EBOOK")
    end_i   = _find_marker(lines, r"\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG EBOOK")

    if start_i is not None and end_i is not None and end_i > start_i:
        main = lines[start_i + 1 : end_i]
        return main

    # Some files have slightly different markers
    start_i2 = _find_marker(lines, r"\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG")
    end_i2   = _find_marker(lines, r"\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG")
    if start_i2 is not None and end_i2 is not None and end_i2 > start_i2:
        return lines[start_i2 + 1 : end_i2]

    # Fallback: remove license block at end if present
    lic_i = _find_marker(lines, r"^\s*START:\s*FULL LICENSE")
    if lic_i is not None and lic_i > 200:  # avoid accidental match at very top
        return lines[:lic_i]

    return lines

def _clean_top_metadata(lines: list[str]) -> list[str]:
    """
    Remove Gutenberg metadata lines that sometimes remain inside the START/END slice.
    Keep the clean title page: 'The Adventures...' + 'by ...' + Contents.
    """
    meta_prefixes = ("title:", "author:", "release date:", "most recently updated:", "language:", "ebook #")

    out = []
    for line in lines:
        s = line.strip()
        low = s.lower()
        if low.startswith(meta_prefixes):
            continue
        # remove stray Gutenberg mentions still inside slice
        if "project gutenberg" in low and ("ebook" in low or "license" in low):
            continue
        out.append(line)

    # Drop leading blank lines
    while out and out[0].strip() == "":
        out.pop(0)

    return out

def _collapse_blank(lines: list[str]) -> list[str]:
    out, prev_blank = [], False
    for line in lines:
        if line.strip() == "":
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        out.append(line)
    return out

def normalize_text_v1(raw: str) -> str:
    lines = raw.splitlines()
    lines = _slice_gutenberg_main(lines)
    lines = _clean_top_metadata(lines)
    lines = _collapse_blank(lines)
    return "\n".join(lines).strip()

def normalize_text_v2(raw: str) -> str:
    """
    v2 editorial:
    - v1
    - convert roman numerals in STORY headers like 'I. A SCANDAL...' → '1. ...'
    - avoid converting roman numerals in Contents list
    """
    text = normalize_text_v1(raw)
    lines = text.splitlines()

    out = []
    in_contents = False
    for line in lines:
        stripped = line.strip()

        # Contents region detection
        if stripped.lower() == "contents":
            in_contents = True

        # Exit contents heuristics once narrative starts
        if in_contents and stripped and stripped.lower().startswith("to sherlock holmes"):
            in_contents = False

        if not in_contents:
            m = re.match(r"^([IVXLCDM]+)\.\s+(.*)", stripped)
            if m:
                n = roman_to_int(m.group(1))
                if n:
                    line = f"{n}. {m.group(2)}"

        out.append(line)

    out = _collapse_blank(out)
    return "\n".join(out).strip()

def write_normalized(book_id: int, text: str, version: str = "v2") -> Tuple[Path, str]:
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    sha = sha256_text(text)
    path = NORMALIZED_DIR / f"book_{book_id:04d}_{version}.txt"
    path.write_text(text, encoding="utf-8")
    return path, sha
