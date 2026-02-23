#!/usr/bin/env python3
# scripts/ops/fix_conan_headings_v4.py
#
# Goal:
# - Take a Conan "Hour of the Dragon" merged TXT and enforce the canonical 22 chapter titles.
# - Convert any detected chapter markers into Markdown headings:
#     ## Chapter N. <Canonical Title>
# - Remove common duplicate "number-title" / "title-only" junk lines around headings.
# - Do NOT rewrite prose; only heading/structure cleanup.
#
# Usage:
#   source .venv/bin/activate
#   python scripts/ops/fix_conan_headings_v4.py \
#     data/books/book_0004/en/runs/v03_fullflow_20260219T213622Z/outputs/agent_shinobi/20260219T215709/book_0004__en__agent_shinobi__MERGED__headingsfix_v1.txt \
#     data/books/book_0004/en/book_0004_refine_clean.md
#
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Canonical chapter titles (Wikisource-consistent)
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


# --- Helpers ---------------------------------------------------------------

def stamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def normalize_title(s: str) -> str:
    """
    Normalize for matching:
    - strip whitespace
    - remove surrounding quotes
    - collapse spaces
    - drop most punctuation (keep letters/numbers/spaces)
    - lowercase
    """
    s = s.strip()

    # normalize fancy quotes/dashes
    s = s.replace("“", '"').replace("”", '"').replace("’", "'")
    s = s.replace("—", "-").replace("–", "-")

    # remove leading/trailing quotes
    s = s.strip('"').strip("'")

    # collapse whitespace
    s = re.sub(r"\s+", " ", s)

    # drop punctuation except spaces/alnum
    s = re.sub(r"[^A-Za-z0-9 ]+", "", s)

    return s.strip().lower()


# Build reverse lookup for matching "title-only" lines
NORM_TO_CH = {normalize_title(v): k for k, v in CANON.items()}

# Patterns for chapter-ish lines
RE_MD_CH = re.compile(r"^##\s+Chapter\s+(\d+)\.\s+(.*)\s*$")
RE_NUM_TITLE = re.compile(r"^\s*(\d{1,2})\s*[-–—]\s*(.+?)\s*$")  # 9 – TITLE
RE_ALLCAPS = re.compile(r"^[A-Z0-9][A-Z0-9 \-\"'!,.:;?]+$")  # A BLACK WIND BLOWS
RE_TITLEISH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-\"'!,.:;?]+$")


def canonical_heading(ch: int) -> str:
    title = CANON.get(ch)
    if not title:
        # Shouldn't happen for Conan; keep safe fallback.
        title = f"(Unknown Chapter {ch})"
    return f"## Chapter {ch}. {title}\n"


def is_duplicate_title_line(candidate: str, ch: int) -> bool:
    """
    True if candidate line is basically the chapter title again (common duplicate).
    """
    cand_norm = normalize_title(candidate)
    canon_norm = normalize_title(CANON[ch])
    return cand_norm == canon_norm


# --- Core transform --------------------------------------------------------

def fix_headings(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0

    while i < len(lines):
        ln = lines[i]
        raw = ln.rstrip("\n")

        # 1) If it's already a Markdown chapter heading, normalize the title by chapter number
        m = RE_MD_CH.match(raw)
        if m:
            ch = int(m.group(1))
            if 1 <= ch <= 22:
                out.append(canonical_heading(ch))

                # remove immediate duplicate title line (common after heading)
                if i + 1 < len(lines):
                    nxt = lines[i + 1].rstrip("\n")
                    if nxt.strip() and is_duplicate_title_line(nxt, ch):
                        i += 2
                        continue
                i += 1
                continue

        # 2) If it's "N - TITLE", and TITLE matches a canonical chapter title, replace with heading
        m = RE_NUM_TITLE.match(raw)
        if m:
            ch = int(m.group(1))

            if 1 <= ch <= 22:
                # If the title part doesn't match canonical, we STILL enforce canonical (your ask).
                out.append(canonical_heading(ch))

                # remove immediate duplicate title line (often the next line is the title again)
                if i + 1 < len(lines):
                    nxt = lines[i + 1].rstrip("\n")
                    if nxt.strip() and is_duplicate_title_line(nxt, ch):
                        i += 2
                        continue

                i += 1
                continue

        # 3) If it's a title-only line (ALLCAPS or Title Case) that matches canonical,
        # convert it to heading using the canonical chapter number.
        s = raw.strip()
        if s and (RE_ALLCAPS.match(s) or RE_TITLEISH.match(s)):
            norm = normalize_title(s)
            ch = NORM_TO_CH.get(norm)
            if ch is not None:
                out.append(canonical_heading(ch))

                # If previous line in out was also the same heading, avoid duplication
                if len(out) >= 2 and out[-2] == out[-1]:
                    out.pop()

                i += 1
                continue

        # default: keep line as-is
        out.append(ln)
        i += 1

    # 4) Deduplicate consecutive identical chapter headings (defensive)
    dedup: list[str] = []
    for ln in out:
        if dedup and ln.startswith("## Chapter ") and dedup[-1] == ln:
            continue
        dedup.append(ln)

    return dedup


def sanity_report(text: str) -> dict:
    headings = re.findall(r"^##\s+Chapter\s+(\d+)\.\s+.*$", text, flags=re.M)
    nums = [int(x) for x in headings]
    dup_nums = sorted({n for n in nums if nums.count(n) > 1})
    return {
        "headings_count": len(nums),
        "dup_chapters": dup_nums,
        "missing_chapters": [n for n in range(1, 23) if n not in nums],
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: fix_conan_headings_v4.py <input.txt> [output.md]")
        return 2

    inp = Path(sys.argv[1])
    outp = Path(sys.argv[2]) if len(sys.argv) >= 3 else inp.with_suffix(".md")

    if not inp.exists():
        print(f"[ERR] Input not found: {inp}")
        return 2

    backup = inp.with_name(inp.name + f".bak_{stamp_utc()}")
    backup.write_text(inp.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    raw_lines = inp.read_text(encoding="utf-8", errors="replace").splitlines(True)

    fixed = fix_headings(raw_lines)
    outp.write_text("".join(fixed), encoding="utf-8")

    rep = sanity_report(outp.read_text(encoding="utf-8", errors="replace"))
    print("[OK] Wrote:", outp)
    print("[OK] Backup:", backup)
    print("Sanity:", rep)

    # non-fatal warning if not all 22 appear (depends on input markers)
    if rep["missing_chapters"]:
        print("[WARN] Missing chapters (no markers found to anchor):", rep["missing_chapters"])
        print("       If you want: we can force-insert missing headings by content anchors,")
        print("       but that becomes heuristic and risks wrong placement.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
