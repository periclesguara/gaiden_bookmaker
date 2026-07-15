from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

ROMAN_RX = r"(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV|XVI|XVII|XVIII|XIX|XX|XXI|XXII|XXIII|XXIV|XXV|XXX|XL|L|LX|LXX|LXXX|XC|C)"

@dataclass
class Unit:
    unit_index: int
    unit_type: str           # book/part/chapter/section/unknown
    title: str
    start_line: int
    end_line: int

# Generic heading detectors (broad coverage)
RX_KEYWORD = re.compile(rf"^\s*(BOOK|Book|PART|Part|CHAPTER|Chapter|SECTION|Section)\s+({ROMAN_RX}|\d+)\b\.?\s*(.*)$")
RX_ROMAN_DOT_TITLE = re.compile(rf"^\s*({ROMAN_RX})\.\s+(.+)$")
RX_ALL_CAPS = re.compile(r"^[A-Z0-9][A-Z0-9 \-—’'\",:;.!?]{6,}$")  # long-ish all caps line
RX_MD_HEADING = re.compile(r"^\s*(#{1,6})\s+(.+)$")

REJECTED_STRUCTURAL_TITLES = {"M.R.C.S.", "L", "FULL TEXT"}

def _clean_title(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _is_rejected_structural_title(title: str) -> bool:
    return _clean_title(title).upper() in REJECTED_STRUCTURAL_TITLES

def detect_units(lines: List[str]) -> List[Unit]:
    """
    Returns structural units that should not be crossed by chunks.
    Heuristics:
      - Markdown headings (#, ##, ...)
      - Keyword headings (CHAPTER 1, BOOK II, PART III, SECTION 4)
      - Roman-dot titles (I. A SCANDAL IN BOHEMIA)
      - Long ALL CAPS lines (as fallback)
    """
    candidates: List[Tuple[int, str, str]] = []  # (line_idx, unit_type, title)

    for i, raw in enumerate(lines):
        line = raw.strip()

        if not line:
            continue

        m = RX_MD_HEADING.match(raw)
        if m:
            level = len(m.group(1))
            title = _clean_title(m.group(2))
            if _is_rejected_structural_title(title):
                continue
            # Map headings: # as book/part, ## as chapter, deeper as section
            unit_type = "book" if level == 1 else "chapter" if level == 2 else "section"
            candidates.append((i, unit_type, title))
            continue

        m = RX_KEYWORD.match(line)
        if m:
            kw = m.group(1).lower()
            num = m.group(2)
            rest = _clean_title(m.group(3) or "")
            unit_type = "chapter" if "chapter" in kw else "book" if "book" in kw else "part" if "part" in kw else "section"
            title = f"{m.group(1)} {num}".strip()
            if rest:
                title = f"{title} — {rest}"
            if _is_rejected_structural_title(title):
                continue
            candidates.append((i, unit_type, title))
            continue

        # Roman-dot title: keep only if it looks like a heading (short-ish and not a normal paragraph)
        m = RX_ROMAN_DOT_TITLE.match(line)
        if m:
            title = _clean_title(m.group(2))
            # Heuristic: treat as unit heading if it's mostly Title/Caps OR short line
            if not _is_rejected_structural_title(title) and len(line) <= 90 and (line.upper() == line or RX_ALL_CAPS.match(line) or title.istitle()):
                candidates.append((i, "chapter", title))
            continue

        # All-caps heading fallback (avoid shouting paragraphs: require short)
        if len(line) <= 90 and RX_ALL_CAPS.match(line):
            title = _clean_title(line)
            if not _is_rejected_structural_title(title):
                candidates.append((i, "section", title.title()))

    # Deduplicate near-duplicates (sometimes two detectors hit the same line)
    candidates = sorted(set(candidates), key=lambda x: x[0])

    # Build units: each candidate starts a unit until next candidate
    units: List[Unit] = []
    if not candidates:
        units.append(Unit(1, "unknown", "FULL TEXT", 0, len(lines) - 1))
        return units

    if candidates[0][0] > 0:
        candidates.insert(0, (0, "unknown", "UNSTRUCTURED TEXT"))

    for idx, (start_i, utype, title) in enumerate(candidates, start=1):
        end_i = (candidates[idx][0] - 1) if idx < len(candidates) else (len(lines) - 1)
        units.append(Unit(idx, utype, title, start_i, end_i))

    # Units are never dropped here: doing so would also drop their source lines.
    return units
