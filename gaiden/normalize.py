from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Tuple

NORMALIZED_DIR = Path("data/normalized")

ROMAN_MAP = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
STANDALONE_CHAPTER_MARKER_RE = re.compile(r"^\s*([IVXLCDM]+|\d+)\.?\s*$", re.IGNORECASE)
EXPLICIT_CHAPTER_RE = re.compile(
    r"^\s*(?:#{1,6}\s+)?(?:chapter|part|section|adventure)\s+([IVXLCDM]+|\d+)\b",
    re.IGNORECASE,
)
RULE_LINE_RE = re.compile(r"^\s*[-=]{5,}\s*$")
DIV_MARKER_RE = re.compile(r"^\s*:::(?:\s+.*)?\s*$")
IMAGE_LINE_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]+\)")
MD_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
NORMALIZED_PART_RE = re.compile(r"^\s*PART\s+(\d+)\b", re.IGNORECASE)
NORMALIZED_CHAPTER_RE = re.compile(r"^\s*CHAPTER\s+(\d+)\b", re.IGNORECASE)

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


def _standalone_chapter_token(line: str) -> str | None:
    match = STANDALONE_CHAPTER_MARKER_RE.match((line or "").strip())
    if not match:
        return None
    return match.group(1)


def _chapter_number(token: str) -> int | None:
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    return roman_to_int(token)


def _normalize_explicit_chapter_heading(line: str) -> str:
    match = re.match(
        r"^(?P<prefix>\s*(?:#{1,6}\s+)?)"
        r"(?P<label>chapter|part|section|adventure)\s+"
        r"(?P<number>[IVXLCDM]+|\d+)"
        r"(?P<suffix>\b.*)$",
        line,
        re.IGNORECASE,
    )
    if not match:
        return line
    chapter_no = _chapter_number(match.group("number"))
    if chapter_no is None:
        return line
    prefix = match.group("prefix") or ""
    label = match.group("label").upper()
    suffix = match.group("suffix") or ""
    return f"{prefix}{label} {chapter_no}{suffix}"


def _looks_like_body_line(line: str) -> bool:
    stripped = (line or "").strip()
    if not stripped:
        return False
    if RULE_LINE_RE.match(stripped) or DIV_MARKER_RE.match(stripped):
        return False
    if IMAGE_LINE_RE.match(stripped) or MD_HEADING_RE.match(stripped):
        return False
    if stripped.startswith("[") and stripped.endswith("]"):
        return False
    if stripped.startswith("\\[") and stripped.endswith("\\]"):
        return False
    words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’-]*", stripped)
    if len(words) < 4:
        return False
    return any(word[:1].islower() for word in words[1:])


def _should_promote_standalone_marker(lines: list[str], idx: int) -> bool:
    token = _standalone_chapter_token(lines[idx])
    if token is None or _chapter_number(token) is None:
        return False

    next_nonblank = ""
    for probe in lines[idx + 1 :]:
        stripped = probe.strip()
        if stripped:
            next_nonblank = stripped
            break
    if not next_nonblank:
        return False
    if _standalone_chapter_token(next_nonblank) is not None:
        return False
    if EXPLICIT_CHAPTER_RE.match(next_nonblank):
        return False
    return _looks_like_body_line(next_nonblank)


def _find_first_body_index(lines: list[str], before_index: int) -> int | None:
    for idx, line in enumerate(lines[:before_index]):
        if _looks_like_body_line(line):
            return idx
    return None


def _promote_standalone_chapter_markers(lines: list[str]) -> list[str]:
    out: list[str] = []
    promoted: list[tuple[int, int]] = []
    in_contents = False

    for idx, line in enumerate(lines):
        stripped = line.strip()

        if stripped.lower() in {"contents", "table of contents"}:
            in_contents = True
        elif in_contents and _looks_like_body_line(stripped):
            in_contents = False

        if not in_contents and _should_promote_standalone_marker(lines, idx):
            token = _standalone_chapter_token(line)
            if token is not None:
                chapter_no = _chapter_number(token)
                if chapter_no is not None:
                    out.append(f"CHAPTER {chapter_no}")
                else:
                    out.append(f"CHAPTER {token}")
                chapter_no = _chapter_number(token)
                if chapter_no is not None:
                    promoted.append((len(out) - 1, chapter_no))
                continue

        out.append(line)

    if promoted and promoted[0][1] == 2 and all(number != 1 for _idx, number in promoted):
        first_body_idx = _find_first_body_index(out, promoted[0][0])
        if first_body_idx is not None:
            prefix: list[str] = []
            if first_body_idx > 0 and out[first_body_idx - 1].strip():
                prefix.append("")
            prefix.append("CHAPTER 1")
            prefix.append("")
            out[first_body_idx:first_body_idx] = prefix

    return out


def _insert_part_markers_for_chapter_resets(lines: list[str]) -> list[str]:
    chapter_positions: list[tuple[int, int]] = []
    for idx, line in enumerate(lines):
        match = NORMALIZED_CHAPTER_RE.match((line or "").strip())
        if not match:
            continue
        chapter_positions.append((idx, int(match.group(1))))

    if not chapter_positions:
        return lines

    part_insertions: list[tuple[int, str]] = []
    part_count = 0

    first_idx, _first_no = chapter_positions[0]
    has_part_before_first = any(
        NORMALIZED_PART_RE.match((lines[idx] or "").strip())
        for idx in range(max(0, first_idx - 3), first_idx)
    )

    resets = []
    previous_no = chapter_positions[0][1]
    for idx, chapter_no in chapter_positions[1:]:
        if chapter_no == 1 and previous_no > 1:
            resets.append(idx)
        previous_no = chapter_no

    if resets and not has_part_before_first:
        part_count = 1
        part_insertions.append((first_idx, "PART 1"))

    for chapter_idx in resets:
        existing_part_before = any(
            NORMALIZED_PART_RE.match((lines[idx] or "").strip())
            for idx in range(max(0, chapter_idx - 3), chapter_idx)
        )
        if existing_part_before:
            continue
        part_count += 1
        if part_count == 0:
            part_count = 2
        part_insertions.append((chapter_idx, f"PART {part_count}"))

    if not part_insertions:
        return lines

    out: list[str] = []
    insertion_map = {idx: label for idx, label in part_insertions}
    for idx, line in enumerate(lines):
        label = insertion_map.get(idx)
        if label is not None:
            if out and out[-1].strip():
                out.append("")
            out.append(label)
            out.append("")
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
    - promote standalone chapter markers like 'II.' / 'V' → 'CHAPTER 2' / 'CHAPTER 5'
    - normalize explicit chapter headings from roman to arabic
    - avoid converting roman numerals in Contents list
    """
    text = normalize_text_v1(raw)
    lines = _promote_standalone_chapter_markers(text.splitlines())

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
            else:
                line = _normalize_explicit_chapter_heading(line)

        out.append(line)

    out = _insert_part_markers_for_chapter_resets(out)
    out = _collapse_blank(out)
    return "\n".join(out).strip()

def write_normalized(book_id: int, text: str, version: str = "v2") -> Tuple[Path, str]:
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    sha = sha256_text(text)
    path = NORMALIZED_DIR / f"book_{book_id:04d}_{version}.txt"
    path.write_text(text, encoding="utf-8")
    return path, sha
