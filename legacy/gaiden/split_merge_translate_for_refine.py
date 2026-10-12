import re
import argparse
from pathlib import Path

# Accept numeric headings like "1. TITLE" (current standard),
# while still allowing legacy "## TITLE" if present.
CHAPTER_RE = re.compile(r"^(?:\d+\.\s+.+|\d+\s*-\s+.+|##\s+.+)$", re.MULTILINE)


def read(p: Path) -> str:
    if not p.exists():
        raise FileNotFoundError(p)
    return p.read_text(encoding="utf-8")


def write(p: Path, s: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def split_chapters(text: str):
    matches = list(CHAPTER_RE.finditer(text))
    if not matches:
        raise ValueError("No chapter headings found (expected 'N. ', 'N - ' or '## ')")

    blocks = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.append(text[start:end].strip())
    return blocks


def split_parts(body: str, parts: int):
    paras = [p for p in body.split("\n\n") if p.strip()]
    if len(paras) < parts:
        size = len(body) // parts
        return [body[i * size : (i + 1) * size] for i in range(parts)]

    total = sum(len(p) for p in paras)
    target = total / parts

    out, buf, acc = [], [], 0
    for p in paras:
        buf.append(p)
        acc += len(p)
        if len(out) < parts - 1 and acc >= target:
            out.append("\n\n".join(buf))
            buf, acc = [], 0

    out.append("\n\n".join(buf))
    return out


def process_language(book: str, lang: str, parts: int):
    root = Path("data/translated") / book / lang
    merge = root / f"merge_translate_{lang}.txt"

    if not merge.exists():
        return 0

    text = read(merge)
    chapters = split_chapters(text)
    outdir = root / "split_chapters_for_refine"

    index = []
    for i, ch in enumerate(chapters, 1):
        lines = ch.splitlines()
        heading = lines[0]
        body = "\n".join(lines[1:]).strip()

        parts_list = split_parts(body, parts)
        for j, part in enumerate(parts_list, 1):
            fn = f"ch_{i:02d}_part_{j:02d}.txt"
            payload = f"{heading}\n\n{part.strip()}\n"
            write(outdir / fn, payload)
            index.append(f"{fn}\tchars={len(payload)}")

    write(outdir / "_INDEX.tsv", "\n".join(index))
    return len(index)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True)
    ap.add_argument("--parts", type=int, default=2)
    args = ap.parse_args()

    book = args.book
    base = Path("data/translated") / book
    langs = [p.name for p in base.iterdir() if p.is_dir()]

    total = 0
    for lang in langs:
        total += process_language(book, lang, args.parts)

    if total == 0:
        raise RuntimeError("No merge_translate_<LANG>.txt files found")

    print(f"[OK] split_for_refine completed: {total} files")


if __name__ == "__main__":
    main()
