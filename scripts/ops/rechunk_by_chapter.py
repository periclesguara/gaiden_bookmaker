#!/usr/bin/env python3
import re
import sys
from pathlib import Path

# Usage:
#   python scripts/ops/rechunk_by_chapter.py <input_txt> <out_dir> --max_chars 8000
#
# Produz:
#   out_dir/ch_001_chunk_001.txt ...
#   out_dir/ch_022_chunk_XXX.txt

CHAPTER_RE = re.compile(r"^\s*(CHAPTER)\s+([0-9]+|[IVXLCDM]+)\b.*$", re.IGNORECASE)
NUM_TITLE_RE = re.compile(r"^\s*([0-9]{1,2})\s*[-–—]\s+.*$")

ROMAN_MAP = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman_to_int(s: str) -> int:
    s = s.upper().strip()
    total = 0
    prev = 0
    for ch in reversed(s):
        val = ROMAN_MAP.get(ch, 0)
        if val < prev:
            total -= val
        else:
            total += val
            prev = val
    return total if total > 0 else 0


def norm_chapter_num(tok: str) -> int:
    tok = tok.strip()
    if tok.isdigit():
        return int(tok)
    return roman_to_int(tok)


def _norm_line_for_match(s: str) -> str:
    s = s.lower()
    s = s.replace("—", "-").replace("–", "-").replace("’", "'")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _detect_markers(lines):
    markers = {}
    for idx, line in enumerate(lines):
        raw = line.rstrip("\n")
        m = CHAPTER_RE.match(raw)
        if m:
            n = norm_chapter_num(m.group(2))
            if n > 0 and n not in markers:
                markers[n] = idx
            continue
        m2 = NUM_TITLE_RE.match(raw)
        if m2:
            n = int(m2.group(1))
            if n > 0 and n not in markers:
                markers[n] = idx

    # Livro comeca no capitulo 1 mesmo sem heading explicito
    markers.setdefault(1, 0)

    # Fallback para headings sumidos por reescrita de agentes
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
            if any(anchor in ln for anchor in anchors):
                markers[chap] = idx
                break
    return markers


def split_chapters(lines):
    markers = _detect_markers(lines)
    # Trabalhamos apenas no range esperado para este livro
    chapter_nums = sorted([n for n in markers if 1 <= n <= 22], key=lambda n: markers[n])
    ordered = [(n, markers[n]) for n in chapter_nums]

    chapters = []
    for i, (n, start) in enumerate(ordered):
        end = ordered[i + 1][1] if i + 1 < len(ordered) else len(lines)
        chapters.append((n, lines[start:end]))
    return chapters, markers


def chunk_lines(ch_lines, max_chars):
    chunks = []
    buf = []
    size = 0
    for line in ch_lines:
        # hard boundary e por capitulo; aqui chunka apenas dentro do capitulo
        ln = len(line)
        if buf and size + ln > max_chars:
            chunks.append("".join(buf).rstrip() + "\n")
            buf = []
            size = 0
        buf.append(line)
        size += ln
    if buf:
        chunks.append("".join(buf).rstrip() + "\n")
    return chunks


def main():
    if len(sys.argv) < 3:
        print("Usage: rechunk_by_chapter.py <input_txt> <out_dir> [--max_chars N]", file=sys.stderr)
        sys.exit(2)
    inp = Path(sys.argv[1]).expanduser()
    out_dir = Path(sys.argv[2]).expanduser()
    max_chars = 8000
    if "--max_chars" in sys.argv:
        i = sys.argv.index("--max_chars")
        max_chars = int(sys.argv[i + 1])

    text = inp.read_text(encoding="utf-8", errors="replace").splitlines(True)
    chapters, markers = split_chapters(text)

    out_dir.mkdir(parents=True, exist_ok=True)

    # Ordena por numero para escrita de arquivos por capitulo
    filtered = sorted(chapters, key=lambda x: x[0])

    # Gera chunks
    for chap_num, chap_lines in filtered:
        if chap_num <= 0:
            continue
        ch_id = f"{chap_num:03d}"
        chunks = chunk_lines(chap_lines, max_chars=max_chars)
        for i, chunk in enumerate(chunks, start=1):
            fn = out_dir / f"ch_{ch_id}_chunk_{i:03d}.txt"
            fn.write_text(chunk, encoding="utf-8")

    # sanity: conta chapters detectados
    chap_nums = [n for n, _ in filtered if n > 0]
    detected = sorted(set(chap_nums))
    missing = [n for n in range(1, 23) if n not in detected]
    print(
        f"[OK] chapters_detected={len(detected)} min={min(detected)} max={max(detected)} "
        f"missing={missing} out={out_dir}"
    )
    print(f"[INFO] marker_map={{{', '.join(f'{k}:{markers[k]}' for k in sorted(markers) if 1 <= k <= 22)}}}")


if __name__ == "__main__":
    main()
