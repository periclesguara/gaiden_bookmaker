#!/usr/bin/env python3
import re
import sys
from pathlib import Path

CH_RE = re.compile(r"^(CHAPTER)\s+([0-9]+)\b(.*)$", re.IGNORECASE)
NUM_TITLE_RE = re.compile(r"^\s*([0-9]{1,2})\s*[-–—]\s+.*$")


def load_titles(tsv_path: Path):
    titles = {}
    for line in tsv_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        n, title = line.split("\t", 1)
        titles[int(n)] = title.strip()
    return titles


def _norm_line_for_match(s: str) -> str:
    s = s.lower()
    s = s.replace("—", "-").replace("–", "-").replace("’", "'")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def detect_chapter_markers(lines: list[str]) -> dict[int, int]:
    markers: dict[int, int] = {}
    for idx, line in enumerate(lines):
        raw = line.rstrip("\n")
        m = CH_RE.match(raw)
        if m:
            n = int(m.group(2))
            markers.setdefault(n, idx)
            continue
        m2 = NUM_TITLE_RE.match(raw)
        if m2:
            n = int(m2.group(1))
            markers.setdefault(n, idx)

    # Inicio do livro = chapter 1
    markers.setdefault(1, 0)

    fallback_anchors = {
        5: [
            "conan lay still, enduring the weight of his chains",
            "conan lay still, bearing the weight of his chains",
        ],
        10: [
            "not all of his guides entered the chamber",
            "not all his guides entered the chamber",
        ],
        18: [
            "conan stared intently at his masked companions",
            "conan stared with burning interest at his masked companions",
        ],
        19: [
            "conan moved carefully toward the light he had seen",
            "conan moved cautiously toward the light he had glimpsed",
        ],
        21: [
            "the drums of peril beat louder",
            "confirmation of the war came when the army of poitain",
            "drums of peril",
        ],
    }
    normalized = [_norm_line_for_match(ln) for ln in lines]
    for chap, anchors in fallback_anchors.items():
        if chap in markers:
            continue
        for idx, ln in enumerate(normalized):
            if any(a in ln for a in anchors):
                markers[chap] = idx
                break
    return markers


def main():
    if len(sys.argv) < 4:
        print("Usage: txt_to_md_chapters.py <input_txt> <titles_tsv> <output_md>", file=sys.stderr)
        sys.exit(2)
    inp = Path(sys.argv[1])
    tsv = Path(sys.argv[2])
    out = Path(sys.argv[3])

    titles = load_titles(tsv)
    lines = inp.read_text(encoding="utf-8", errors="replace").splitlines(True)

    markers = detect_chapter_markers(lines)
    ordered = sorted(
        ((n, idx) for n, idx in markers.items() if 1 <= n <= 22),
        key=lambda x: x[1],
    )
    marker_by_idx = {idx: n for n, idx in ordered}

    out_lines = []
    for idx, line in enumerate(lines):
        if idx in marker_by_idx:
            num = marker_by_idx[idx]
            title = titles.get(num, "").strip()
            if title:
                out_lines.append(f"## Chapter {num} -- {title}\n")
            else:
                out_lines.append(f"## Chapter {num}\n")
        out_lines.append(line)

    # normaliza 3+ linhas em branco -> 2
    text = "".join(out_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    out.write_text(text, encoding="utf-8")
    print(f"[OK] wrote {out}")
    print(f"[INFO] chapters_detected={len(marker_by_idx)}")


if __name__ == "__main__":
    main()
