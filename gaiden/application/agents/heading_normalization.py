from __future__ import annotations

import re


ROMAN_TO_INT = {
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
}

STRUCTURAL_HEADING_RE = re.compile(
    r"^(?P<prefix>\s*(?:#{1,6}\s*)?)(?P<label>BOOK|CHAPTER|PART|SECTION)"
    r"(?P<space>\s+)(?P<roman>[IVXLCDM]+)(?P<tail>\b.*)$",
    re.IGNORECASE,
)


def normalize_roman_heading_numerals(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        match = STRUCTURAL_HEADING_RE.match(line)
        if not match:
            lines.append(line)
            continue
        roman = match.group("roman").upper()
        number = ROMAN_TO_INT.get(roman)
        if number is None:
            lines.append(line)
            continue
        lines.append(
            f"{match.group('prefix')}{match.group('label')}{match.group('space')}{number}{match.group('tail')}"
        )
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
