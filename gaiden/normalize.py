from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Tuple

NORMALIZED_DIR = Path("data/normalized")

ROMAN_MAP = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
STRUCTURAL_KEYWORDS = ("CHAPTER", "ADVENTURE", "PART", "BOOK")

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


def _find_any_marker(lines: list[str], patterns: list[str], start_at: int = 0) -> int | None:
    rx_list = [re.compile(p, re.IGNORECASE) for p in patterns]
    for i in range(start_at, len(lines)):
        line = lines[i]
        for rx in rx_list:
            if rx.search(line):
                return i
    return None


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

TAIL_LICENSE_MARKERS = [
    "end of the project gutenberg",
    "project gutenberg",
    "gutenberg license",
    "www.gutenberg.org",
    "this ebook is for the use of anyone anywhere",
    "full project gutenberg license",
    "start: full license",
    "end: full license",
]

FRONTMATTER_MARKERS = [
    "project gutenberg",
    "gutenberg license",
    "www.gutenberg.org",
    "this ebook is for the use of anyone anywhere",
    "copyright",
    "all rights reserved",
    "© 202",
    "mantaquest",
    "rinobooks",
    "illustrated edition",
    "translated by",
    "edited by",
    "adapted by",
    "this edition was",
    "this edition has been",
]


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
    if re.match(r"^(CHAPTER|ADVENTURE|PART|BOOK)\s+(\d+|[IVXLCDM]+)\b", s, flags=re.IGNORECASE):
        return True
    if re.match(r"^(\d+)\s*[\.\-]\s+.+$", s):
        return True
    if re.match(r"^[IVXLCDM]+\.\s+.+$", s):
        return True
    return False


def _is_roman_only(line: str) -> bool:
    s = line.strip()
    return bool(re.fullmatch(r"[IVXLCDM]+", s, flags=re.IGNORECASE))


def _find_first_heading_index(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        if _is_heading_line(line):
            return i
        if _is_roman_only(line):
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and lines[j].strip():
                return i
    return None


def _looks_like_toc_block(block_lines: list[str]) -> bool:
    text = "\n".join(block_lines).lower()
    if "contents" in text or "table of contents" in text or "index" in text:
        return True
    lines = [ln for ln in block_lines if ln.strip()]
    if len(lines) < 3:
        return False
    toc_line = re.compile(r"^(chapter|adventure|part|book)?\s*[ivxlc0-9]+\b.*\d+\s*$", re.IGNORECASE)
    dot_leader = re.compile(r"\.{2,}\s*\d+\s*$")
    hits = sum(1 for ln in lines if toc_line.search(ln) or dot_leader.search(ln))
    return hits >= 3 and hits / len(lines) >= 0.5


def _strip_legacy_frontmatter(lines: list[str], max_lines: int = 200) -> tuple[list[str], int]:
    limit = min(len(lines), max_lines)
    head = lines[:limit]
    tail = lines[limit:]

    first_heading = _find_first_heading_index(head)
    if first_heading is None:
        first_heading = limit

    kept: list[str] = []
    removed = 0

    i = 0
    while i < len(head):
        if i >= first_heading:
            kept.extend(head[i:])
            break

        if head[i].strip() == "":
            kept.append(head[i])
            i += 1
            continue

        j = i
        block: list[str] = []
        while j < len(head) and head[j].strip() != "":
            block.append(head[j])
            j += 1

        block_text = "\n".join(block).lower()
        is_marker = any(m in block_text for m in FRONTMATTER_MARKERS)
        if is_marker and not _looks_like_toc_block(block):
            removed += len(block)
        else:
            kept.extend(block)
        i = j

    return kept + tail, removed


def _strip_trailing_license(lines: list[str], min_chars: int = 10000, tail_window: int = 300) -> tuple[list[str], int]:
    if not lines:
        return lines, 0
    text = "\n".join(lines)
    has_heading = _find_first_heading_index(lines) is not None
    if len(text) < min_chars and not has_heading:
        return lines, 0

    start = max(0, len(lines) - tail_window)
    marker_idx: int | None = None
    for i in range(start, len(lines)):
        low = lines[i].lower()
        if any(m in low for m in TAIL_LICENSE_MARKERS):
            marker_idx = i if marker_idx is None else min(marker_idx, i)
    if marker_idx is None:
        return lines, 0

    removed = len(lines) - marker_idx
    return lines[:marker_idx], removed


def normalize_text_policy_v1_en_clean(raw: str) -> tuple[str, dict]:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.splitlines()

    start_idx = _find_any_marker(lines, START_MARKERS)
    end_idx = _find_any_marker(lines, END_MARKERS, start_at=(start_idx + 1) if start_idx is not None else 0)

    head_removed = 0
    tail_removed = 0
    start_found = start_idx is not None
    end_found = end_idx is not None and start_idx is not None and end_idx > start_idx

    if start_idx is not None:
        head_removed += start_idx + 1
        lines = lines[start_idx + 1 :]
        if end_idx is not None:
            tail_removed += len(lines) - (end_idx - start_idx - 1)
            lines = lines[: end_idx - start_idx - 1]

    # remove Gutenberg metadata lines that might remain
    lines = _clean_top_metadata(lines)

    # legacy frontmatter (conservative)
    lines, removed_head = _strip_legacy_frontmatter(lines)
    head_removed += removed_head

    # endnotes / trailing license
    lines, removed_tail = _strip_trailing_license(lines)
    tail_removed += removed_tail

    lines = _collapse_blank_max(lines, max_blank=2)
    cleaned = "\n".join(lines).strip()

    normalized = normalize_text_policy_v1_en(cleaned)
    stats = {
        "start_found": start_found,
        "end_found": end_found,
        "head_removed": head_removed,
        "tail_removed": tail_removed,
    }
    return normalized, stats

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


def _has_structural_keyword(line: str) -> bool:
    upper = line.upper()
    return any(re.search(rf"\b{kw}\b", upper) for kw in STRUCTURAL_KEYWORDS)


def _is_numeric_residue(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if _has_structural_keyword(s):
        return False
    if re.fullmatch(r"[\(\)\-\s]*\d+[\)\.\-]*\s*", s):
        return True
    if re.fullmatch(r"[\(\)\-\s]*[IVXLCDM]+[\)\.\-]*\s*", s, flags=re.IGNORECASE):
        return True
    return False


def _roman_to_arabic_structural(line: str) -> str:
    m = re.match(
        r"^(\s*)(CHAPTER|ADVENTURE|PART|BOOK)\s+([IVXLCDM]+)\b(.*)$",
        line,
        flags=re.IGNORECASE,
    )
    if m:
        n = roman_to_int(m.group(3))
        if n:
            return f"{m.group(1)}{m.group(2).upper()} {n}{m.group(4)}"
    m = re.match(r"^(\s*)([IVXLCDM]+)\s*[\.\-]\s+(.+)$", line, flags=re.IGNORECASE)
    if m:
        n = roman_to_int(m.group(2))
        if n:
            return f"{m.group(1)}{n}. {m.group(3)}"
    # Standalone roman numeral residue (e.g., "I."), convert to arabic for merge pass
    m = re.match(r"^(\s*)([IVXLCDM]+)\s*([\.])?\s*$", line, flags=re.IGNORECASE)
    if m:
        n = roman_to_int(m.group(2))
        if n:
            punct = m.group(3) or ""
            return f"{m.group(1)}{n}{punct}"
    return line


def _normalize_heading_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title.strip())
    return title.upper()

def _looks_like_title(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if len(s) > 120:
        return False
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ’']+", s)
    if len(words) > 12:
        return False
    if "," in s or ";" in s or ":" in s:
        return False
    # Titles shouldn't end with sentence punctuation
    if re.search(r"[.!?]$", s):
        return False
    return True

def _looks_like_heading_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if re.match(r"^(\d+)\s*-\s*(.+)$", s):
        return True
    if re.match(r"^(CHAPTER|ADVENTURE|PART|BOOK)\s+(\d+)\b", s, flags=re.IGNORECASE):
        return True
    if re.match(r"^(\d+)\s*[\.\-]\s+(.+)$", s):
        return True
    if _is_numeric_residue(s):
        return True
    return False


def _english_heuristic(text: str) -> None:
    sample = text[:2000].lower()
    if not sample:
        raise ValueError("Language check failed: empty text.")
    en_markers = [" the ", " and ", " of ", " to ", " i ", " not ", " sherlock", " watson"]
    score = sum(sample.count(m) for m in en_markers)
    if score < 3:
        raise ValueError("Language check failed: text does not look like English.")


def normalize_text_policy_v1_en(raw: str) -> str:
    """
    GAIDEN normalize_policy_v1_en:
    - Pass 1: remove numeric residues (arabic + roman), unless structural/heading context.
    - Pass 2: convert roman numerals to arabic (structural only).
    - Pass 3/4/5: detect chapter boundaries, merge number+title, enforce 'N - TITLE'.
    - Pass 6: validation (hard fail).
    """
    lines = raw.splitlines()

    # Pass 1: numeric residue cleaning (with lookahead for split headings)
    cleaned: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_numeric_residue(line):
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and _looks_like_title(lines[j]):
                cleaned.append(line)
            i += 1
            continue
        cleaned.append(line)
        i += 1

    # Pass 2: roman -> arabic (structural only)
    converted = [_roman_to_arabic_structural(line) for line in cleaned]

    # Pass 3/4/5: detect + merge + enforce canonical heading
    out: list[str] = []
    headings: list[int] = []
    i = 0
    seen_body = False
    in_toc = False
    while i < len(converted):
        line = converted[i].strip()
        if not line:
            out.append("")
            i += 1
            continue

        next_line = converted[i + 1].strip() if i + 1 < len(converted) else ""

        # Detect and skip table of contents block
        if line.lower() == "contents":
            in_toc = True
            i += 1
            continue
        if in_toc:
            if line == "":
                i += 1
                continue
            if _looks_like_heading_line(line):
                # look ahead to decide if this is still TOC or real chapter start
                j = i + 1
                while j < len(converted) and converted[j].strip() == "":
                    j += 1
                # skip pure numeric residue in TOC lookahead
                while j < len(converted) and _is_numeric_residue(converted[j].strip()):
                    j += 1
                if j < len(converted) and _looks_like_heading_line(converted[j].strip()):
                    i += 1
                    continue
                # found non-heading body: TOC ends here
                in_toc = False
            else:
                in_toc = False

        heading_num: int | None = None
        heading_title: str | None = None
        consume_next = False

        m = re.match(r"^(\d+)\s*-\s*(.+)$", line)
        if m:
            heading_num = int(m.group(1))
            heading_title = m.group(2)
        else:
            m = re.match(r"^(CHAPTER|ADVENTURE|PART|BOOK)\s+(\d+)\b\s*[\.\-:]?\s*(.*)$", line, flags=re.IGNORECASE)
            if m:
                heading_num = int(m.group(2))
                heading_title = m.group(3).strip() or None
                if not heading_title and next_line and _looks_like_title(next_line):
                    heading_title = next_line
                    consume_next = True
            else:
                m = re.match(r"^(\d+)\s*[\.\-]\s+(.+)$", line)
                if m:
                    heading_num = int(m.group(1))
                    heading_title = m.group(2)
                else:
                    if _is_numeric_residue(line):
                        num_match = re.match(r"^\D*(\d+)\D*$", line)
                        if num_match:
                            j = i + 1
                            while j < len(converted) and converted[j].strip() == "":
                                j += 1
                            if j < len(converted) and _looks_like_title(converted[j]):
                                # ignore residue immediately after a real heading (even with blank lines)
                                last_non_empty = None
                                for prev in reversed(out):
                                    if prev.strip():
                                        last_non_empty = prev.strip()
                                        break
                                if last_non_empty and re.match(r"^\d+\s+-\s+", last_non_empty):
                                    i += 1
                                    continue
                                heading_num = int(num_match.group(1))
                                heading_title = converted[j].strip()
                                consume_next = True

        if heading_num is not None and heading_title:
            # Skip TOC-like heading blocks before body starts
            if not seen_body:
                k = i + 1
                while k < len(converted) and converted[k].strip() == "":
                    k += 1
                if k < len(converted) and _looks_like_heading_line(converted[k]):
                    i += 1
                    continue
            canon = f"{heading_num} - {_normalize_heading_title(heading_title)}"
            out.append(canon)
            headings.append(heading_num)
            if consume_next:
                i += 2
                continue
            i += 1
            continue

        out.append(converted[i])
        if converted[i].strip():
            seen_body = True
        i += 1

    normalized = "\n".join(_collapse_blank(out)).strip()

    # Pass 6: validation
    for ln in normalized.splitlines():
        if _is_numeric_residue(ln):
            raise ValueError(f"Normalize validation failed: numeric residue line: {ln!r}")
        if re.search(r"\b(CHAPTER|ADVENTURE|PART|BOOK)\s+[IVXLCDM]+\b", ln, flags=re.IGNORECASE):
            raise ValueError(f"Normalize validation failed: roman structural remains: {ln!r}")
        if re.match(r"^[IVXLCDM]+\s*[\.\-]\s+", ln, flags=re.IGNORECASE):
            raise ValueError(f"Normalize validation failed: roman heading remains: {ln!r}")
        if re.match(r"^(CHAPTER|ADVENTURE|PART|BOOK)\b\s*(\d+|[IVXLCDM]+)", ln, flags=re.IGNORECASE):
            raise ValueError(f"Normalize validation failed: non-canonical heading: {ln!r}")
        if re.match(r"^\d+\.\s+", ln):
            raise ValueError(f"Normalize validation failed: non-canonical heading: {ln!r}")

    # NOTE: Do not hard-fail on chapter numbering gaps/resets.
    # Some sources include TOC-like numbering or non-sequential headings;
    # we keep output deterministic without blocking the pipeline.

    _english_heuristic(normalized)
    return normalized

def write_normalized(book_id: int, text: str, version: str = "v2") -> Tuple[Path, str]:
    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    sha = sha256_text(text)
    path = NORMALIZED_DIR / f"book_{book_id:04d}_{version}.txt"
    path.write_text(text, encoding="utf-8")
    return path, sha
