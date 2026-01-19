#!/usr/bin/env python3
from pathlib import Path

from gaiden.chapter_chunks import build_chapter_chunks

# Texto normalizado vindo do pipeline (normalize v2).
INPUT = Path("data/normalized/book_0001_v2.txt")

# Texto normalizado com headings de capitulo.
OUTPUT = Path("data/normalized/book_0001_v2_chapterized.txt")

# Chunks por capitulo.
CHUNKS_DIR = Path("data/chunks/book_0001/split_01_by_chapter")
MANIFEST_PATH = CHUNKS_DIR / "chunks_by_chapter.json"


def main() -> None:
    if not INPUT.exists():
        raise SystemExit(f"Input nao encontrado: {INPUT}")

    raw = INPUT.read_text(encoding="utf-8")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    result = build_chapter_chunks(raw, CHUNKS_DIR, MANIFEST_PATH, language="en")
    OUTPUT.write_text(result["normalized_text"], encoding="utf-8")
    print(f"OK: escrito em {OUTPUT}")
    print(f"OK: chunks por capitulo em {CHUNKS_DIR}")
    print(f"OK: manifesto em {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
