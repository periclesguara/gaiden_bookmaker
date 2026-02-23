#!/usr/bin/env python3
"""
Conan HotD headings V42 (force anchors, safe mode)

- Keeps existing chapter headings and normalizes heading titles to canonical text.
- For missing chapters, injects canonical headings only at explicit regex anchors.
- Requires exactly one anchor match per missing chapter (no guessing).
- Drops duplicate heading lines for the same chapter number.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CANON = {
    1: "O Sleeper, Awake!",
    2: "The Black Wind Blows",
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
    13: '"A Ghost Out of the Past"',
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

RE_HEADING = re.compile(r"^##\s+Chapter\s+(\d+)\.\s+.*$", re.I)


def stamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def canonical_heading(ch: int) -> str:
    return f"## Chapter {ch}. {CANON[ch]}\n"


def sanity(text: str) -> dict:
    nums = [int(x) for x in re.findall(r"^##\s+Chapter\s+(\d+)\.", text, flags=re.M | re.I)]
    dup = sorted({n for n in nums if nums.count(n) > 1})
    missing = [n for n in range(1, 23) if n not in nums]
    return {"headings_count": len(nums), "dup_chapters": dup, "missing_chapters": missing}


def parse_anchors(path: Path) -> dict[int, str]:
    anchors: dict[int, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" not in line:
            raise ValueError(f"anchors:{lineno}: expected '<chapter>\\t<regex_anchor>'")
        left, pattern = line.split("\t", 1)
        try:
            ch = int(left.strip())
        except ValueError as exc:
            raise ValueError(f"anchors:{lineno}: invalid chapter number: {left!r}") from exc
        if ch < 1 or ch > 22:
            raise ValueError(f"anchors:{lineno}: chapter out of range (1..22): {ch}")
        pattern = pattern.strip()
        if not pattern:
            raise ValueError(f"anchors:{lineno}: empty regex anchor")
        if ch in anchors:
            raise ValueError(f"anchors:{lineno}: duplicate chapter in anchors: {ch}")
        anchors[ch] = pattern
    return anchors


def normalize_existing_headings(lines: list[str]) -> tuple[list[str], set[int]]:
    out: list[str] = []
    seen: set[int] = set()
    for raw in lines:
        m = RE_HEADING.match(raw.rstrip("\n"))
        if m:
            ch = int(m.group(1))
            if 1 <= ch <= 22:
                if ch in seen:
                    continue
                out.append(canonical_heading(ch))
                seen.add(ch)
                continue
        out.append(raw)
    return out, seen


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: fix_conan_headings_v42.py <input.md> <anchors.txt> [output.md]")
        return 2

    inp = Path(sys.argv[1])
    anchp = Path(sys.argv[2])
    outp = Path(sys.argv[3]) if len(sys.argv) >= 4 else inp.with_suffix(".v42.md")

    if not inp.exists():
        print(f"[ERR] Input not found: {inp}")
        return 2
    if not anchp.exists():
        print(f"[ERR] Anchors file not found: {anchp}")
        return 2

    try:
        anchors = parse_anchors(anchp)
    except ValueError as exc:
        print(f"[ERR] {exc}")
        return 2

    backup = inp.with_name(inp.name + f".bak_{stamp_utc()}")
    backup.write_text(inp.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    src_lines = inp.read_text(encoding="utf-8", errors="replace").splitlines(True)

    clean_lines, present = normalize_existing_headings(src_lines)
    missing = [n for n in range(1, 23) if n not in present]

    # Safe mode: every missing chapter must have an explicit anchor.
    missing_without_anchor = [n for n in missing if n not in anchors]
    if missing_without_anchor:
        print("[ERR] Missing chapters without anchors:", missing_without_anchor)
        return 2

    insert_by_idx: dict[int, list[int]] = {}
    for ch in missing:
        pattern = anchors[ch]
        try:
            rex = re.compile(pattern)
        except re.error as exc:
            print(f"[ERR] Invalid regex for chapter {ch}: {pattern!r} ({exc})")
            return 2

        hits = [i for i, ln in enumerate(clean_lines) if rex.search(ln.rstrip("\n"))]
        if len(hits) == 0:
            print(f"[ERR] Anchor not found for chapter {ch}: {pattern!r}")
            return 2
        if len(hits) > 1:
            print(f"[ERR] Anchor matched multiple lines for chapter {ch}: {pattern!r} (hits={len(hits)})")
            return 2
        idx = hits[0]
        insert_by_idx.setdefault(idx, []).append(ch)

    out_lines: list[str] = []
    emitted: set[int] = set()

    for i, raw in enumerate(clean_lines):
        for ch in sorted(insert_by_idx.get(i, [])):
            if ch not in emitted:
                out_lines.append(canonical_heading(ch))
                emitted.add(ch)

        m = RE_HEADING.match(raw.rstrip("\n"))
        if m:
            ch = int(m.group(1))
            if 1 <= ch <= 22:
                emitted.add(ch)
        out_lines.append(raw)

    text = "".join(out_lines)
    outp.write_text(text, encoding="utf-8")

    rep = sanity(text)
    print("[OK] Wrote:", outp)
    print("[OK] Backup:", backup)
    print("[OK] Missing before:", missing)
    print("Sanity:", rep)
    if rep["dup_chapters"] or rep["missing_chapters"] or rep["headings_count"] != 22:
        print("[WARN] Sanity not fully clean; review anchors/order before replacing canonical file.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
