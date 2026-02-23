#!/usr/bin/env python3
# scripts/ops/fix_conan_headings_v41.py
#
# Conan HotD headings: robust detector + safe injection
# - Detects chapter markers in multiple real-world formats:
#   * "## Chapter N. ...", "CHAPTER N", "CHAPTER X", "Chapter X."
#   * "N - TITLE", "N - TITLE", "N. TITLE", "N) TITLE", "N: TITLE"
#   * Title-only lines (caps or titlecase)
#   * Lines that contain the canonical title (fuzzy, punctuation-insensitive)
# - Enforces canonical 22 titles and avoids duplicates
# - Removes only the chapter marker line itself if it is an "isca" (number-title / title-only),
#   keeping the narrative text intact.
#
# Usage:
#   source .venv/bin/activate
#   python scripts/ops/fix_conan_headings_v41.py \
#     <input.txt or .md> \
#     <output.md>
#
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CANON = {
    1: "O Sleeper, Awake!",
    2: "The Hour of the Dragon",
    3: "The Cliffs Reel",
    4: '"From What Hell Have You Crawled?"',
    5: "The Haunter of the Pits",
    6: "The Thrust of a Knife",
    7: "The Rending of the Veil",
    8: "Dying Embers",
    9: '"It Is the King or His Ghost!"',
    10: "A Coin from Acheron",
    11: "Swords of the South",
    12: "The Fang of the Dragon",
    13: "A Ghost Out of the Past",
    14: "The Black Hand of Set",
    15: "The Return of the Corsair",
    16: "Black-Walled Khemi",
    17: '"He Has Slain the Sacred Son of Set!"',
    18: '"I Am the Woman Who Never Died"',
    19: "In the Hall of the Dead",
    20: "Out of the Dust Shall Acheron Arise",
    21: "Drums of Peril",
    22: "The Road to Acheron",
}

ROMAN = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
    "XIII": 13,
    "XIV": 14,
    "XV": 15,
    "XVI": 16,
    "XVII": 17,
    "XVIII": 18,
    "XIX": 19,
    "XX": 20,
    "XXI": 21,
    "XXII": 22,
}


def stamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def normalize_title(s: str) -> str:
    s = s.strip()
    # normalize unicode quotes/dashes
    s = s.replace("“", '"').replace("”", '"').replace("’", "'")
    s = s.replace("—", "-").replace("–", "-")
    # collapse whitespace
    s = re.sub(r"\s+", " ", s)
    # remove surrounding quotes
    s = s.strip('"').strip("'")
    # drop punctuation (keep alnum + spaces)
    s = re.sub(r"[^A-Za-z0-9 ]+", "", s)
    return s.strip().lower()


CANON_NORM = {ch: normalize_title(title) for ch, title in CANON.items()}
NORM_TO_CH = {}
for ch, norm in CANON_NORM.items():
    # guard collision: if collision occurs, keep first (good enough here)
    NORM_TO_CH.setdefault(norm, ch)


def canonical_heading(ch: int) -> str:
    return f"## Chapter {ch}. {CANON[ch]}\n"


RE_MD_CH = re.compile(r"^##\s+Chapter\s+(\d+)\.\s+.*$", re.I)

# Formats like:
#  9 - TITLE
#  9 - TITLE
#  9. TITLE
#  9) TITLE
#  9: TITLE
RE_NUM_TITLE = re.compile(r"^\s*(\d{1,2})\s*(?:[-–—]|[.)]:?)\s+(.+?)\s*$")

# CHAPTER X / Chapter X.
RE_CHAPTER_ROMAN = re.compile(r"^\s*(?:CHAPTER|Chapter)\s+([IVX]{1,6})\.?\s*$")

# CHAPTER 9 / Chapter 9.
RE_CHAPTER_ARABIC = re.compile(r"^\s*(?:CHAPTER|Chapter)\s+(\d{1,2})\.?\s*$")

# Title-only lines (caps or titlecase-ish)
RE_TITLEISH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-\"'!,.:;?]+$")


def looks_like_marker_line(raw: str) -> bool:
    s = raw.strip()
    if not s:
        return False
    if RE_NUM_TITLE.match(s):
        return True
    if RE_CHAPTER_ROMAN.match(s) or RE_CHAPTER_ARABIC.match(s):
        return True
    # ALLCAPS title-ish
    if s == s.upper() and len(s) >= 8 and RE_TITLEISH.match(s):
        return True
    # Title-ish short-ish line
    if RE_TITLEISH.match(s) and len(s) <= 80:
        # Could be prose; we only treat as marker if it matches canonical title
        return normalize_title(s) in NORM_TO_CH
    return False


def infer_ch_from_line(raw: str) -> int | None:
    s = raw.strip()
    m = RE_MD_CH.match(s)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 22 else None

    m = RE_CHAPTER_ARABIC.match(s)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 22 else None

    m = RE_CHAPTER_ROMAN.match(s)
    if m:
        r = m.group(1).upper()
        return ROMAN.get(r)

    m = RE_NUM_TITLE.match(s)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 22 else None

    # Title-only exact match
    norm = normalize_title(s)
    return NORM_TO_CH.get(norm)


def line_contains_canon_title(raw: str) -> int | None:
    """
    Fallback: if a line contains a canonical title (fuzzy),
    e.g. "... 10 - A COIN FROM ACHERON ..." or "A COIN FROM ACHERON"
    """
    norm = normalize_title(raw)
    if not norm:
        return None
    # try each canonical normalized title as substring
    for ch, tnorm in CANON_NORM.items():
        if tnorm and tnorm in norm:
            return ch
    return None


def sanity(text: str) -> dict:
    nums = [int(x) for x in re.findall(r"^##\s+Chapter\s+(\d+)\.", text, flags=re.M | re.I)]
    dup = sorted({n for n in nums if nums.count(n) > 1})
    missing = [n for n in range(1, 23) if n not in nums]
    return {"headings_count": len(nums), "dup_chapters": dup, "missing_chapters": missing}


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: fix_conan_headings_v41.py <input> [output]")
        return 2

    inp = Path(sys.argv[1])
    outp = Path(sys.argv[2]) if len(sys.argv) >= 3 else inp.with_suffix(".md")

    if not inp.exists():
        print(f"[ERR] Input not found: {inp}")
        return 2

    # backup input
    backup = inp.with_name(inp.name + f".bak_{stamp_utc()}")
    backup.write_text(inp.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")

    lines = inp.read_text(encoding="utf-8", errors="replace").splitlines(True)

    out: list[str] = []
    seen_ch: set[int] = set()

    i = 0
    while i < len(lines):
        raw = lines[i]
        s = raw.rstrip("\n")

        # If already a heading, normalize to canonical and keep it
        m = re.match(r"^##\s+Chapter\s+(\d+)\.", s, flags=re.I)
        if m:
            ch = int(m.group(1))
            if 1 <= ch <= 22 and ch not in seen_ch:
                out.append(canonical_heading(ch))
                seen_ch.add(ch)
            # skip duplicate headings for same chapter
            i += 1
            continue

        # Primary detection on marker lines
        ch = infer_ch_from_line(s)
        if ch and (1 <= ch <= 22) and (ch not in seen_ch):
            out.append(canonical_heading(ch))
            seen_ch.add(ch)

            # If this line is a marker line (num-title, chapter x, title-only), drop it
            # so we don't keep confusing "frase solta" right under heading.
            if looks_like_marker_line(s):
                i += 1
                # Also drop immediate next line if it repeats the title-only
                if i < len(lines):
                    nxt = lines[i].rstrip("\n")
                    if normalize_title(nxt) == CANON_NORM[ch]:
                        i += 1
                continue

            i += 1
            continue

        # Fallback: if line contains canonical title (fuzzy), inject heading above it
        ch2 = line_contains_canon_title(s)
        if ch2 and (ch2 not in seen_ch):
            out.append(canonical_heading(ch2))
            seen_ch.add(ch2)

            # If the line itself is basically just the marker/title, drop it
            if looks_like_marker_line(s) or normalize_title(s) == CANON_NORM[ch2]:
                i += 1
                continue

            # Otherwise keep the line (it might be prose mentioning the phrase)
            out.append(raw)
            i += 1
            continue

        # default keep
        out.append(raw)
        i += 1

    text = "".join(out)
    outp.write_text(text, encoding="utf-8")

    rep = sanity(text)
    print("[OK] Wrote:", outp)
    print("[OK] Backup:", backup)
    print("Sanity:", rep)

    # If still missing, we DO NOT force-insert by heuristics here (that's where chapter leaks happen).
    # You can add a force mode later if you really want to.
    if rep["missing_chapters"]:
        print("[WARN] Still missing chapters (no reliable markers found):", rep["missing_chapters"])
        print("       If you want a 'force 22' mode, do it with explicit anchors you choose.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
