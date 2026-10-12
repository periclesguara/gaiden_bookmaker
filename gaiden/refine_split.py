from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from gaiden.translate_artifacts import normalize_book_code, resolve_active_or_latest

# Accept chapter headings used by current/legacy pipelines:
# - "1. TITLE"
# - "1 - TITLE" / "1 – TITLE" / "1 — TITLE"
# - "**1 – TITLE**"
# - "## TITLE"
# - "CHAPTER IV. TITLE"
CHAPTER_LINE_PATTERN = (
    r"(?:\*{0,2}\s*\d+\s*[.\-–—]\s+[^\na-z]+\*{0,2}"
    r"|(?i:\*{0,2}\s*CHAPTER\s+(?:[IVXLCDM]+|\d+)\b.*\*{0,2})"
    r"|##\s+.+)"
)
CHAPTER_RE = re.compile(rf"^{CHAPTER_LINE_PATTERN}$", re.MULTILINE)
CHAPTER_LINE_RE = re.compile(rf"^{CHAPTER_LINE_PATTERN}$")


def read(p: Path) -> str:
    if not p.exists():
        raise FileNotFoundError(p)
    return p.read_text(encoding="utf-8")


def write(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def _reset_split_dir(outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    for fp in outdir.glob("ch_*_part_*.txt"):
        if fp.is_file():
            fp.unlink()
    idx = outdir / "_INDEX.tsv"
    if idx.exists():
        idx.unlink()


def _looks_like_chapter_body(block: str) -> bool:
    sample = (block or "").strip()
    if len(sample) < 280:
        return False
    alpha = sum(1 for ch in sample if ch.isalpha())
    lower = sum(1 for ch in sample if ch.islower())
    return alpha >= 140 and lower >= 60


def split_chapters(text: str):
    matches = list(CHAPTER_RE.finditer(text))
    if not matches:
        raise ValueError("No chapter headings found (expected 'N. ', 'N - ' or '## ')")

    # Skip heading candidates from TOC/index blocks: choose the first heading
    # whose following block looks like real prose body (not only title/index lines).
    first_body_idx = 0
    for idx, m in enumerate(matches):
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        between = text[m.end() : next_start]
        if _looks_like_chapter_body(between):
            first_body_idx = idx
            break

    if first_body_idx > 0:
        matches = matches[first_body_idx:]

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


def _normalize_heading(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("*", "").strip())


def _strip_leading_heading(chapter_text: str, heading: str) -> str:
    lines = chapter_text.splitlines()
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        if _normalize_heading(line) == _normalize_heading(heading):
            return "\n".join(lines[idx + 1 :]).strip()
        break
    return chapter_text.strip()


def _process_language_from_manifest(book: str, lang: str, parts: int) -> int:
    book_code = normalize_book_code(book)
    translated_root = Path("data/translated") / book_code / lang
    manifest_path = Path("data/chunks") / book_code / "en" / "chunks_manifest.json"
    if not manifest_path.exists():
        return 0

    payload = json.loads(read(manifest_path))
    chapters = payload.get("chapters") or []
    if not chapters:
        return 0

    outdir = translated_root / "split_chapters_for_refine"
    _reset_split_dir(outdir)
    index: list[str] = []

    out_chapter_idx = 0
    for chapter in chapters:
        chunk_items = chapter.get("chunks") or []
        if not chunk_items:
            continue

        heading = (chapter.get("heading_line") or "").strip()
        chunk_texts: list[str] = []
        for item in chunk_items:
            rel = item.get("file_path")
            if not rel:
                continue
            fp = translated_root / rel
            if not fp.exists():
                raise FileNotFoundError(f"Missing translated chunk for split: {fp}")
            chunk_texts.append(read(fp).strip())

        if not chunk_texts:
            continue

        out_chapter_idx += 1
        chapter_text = "\n\n".join(t for t in chunk_texts if t).strip()
        body = _strip_leading_heading(chapter_text, heading) if heading else chapter_text
        parts_list = split_parts(body, parts)

        for j, part in enumerate(parts_list, 1):
            fn = f"ch_{out_chapter_idx:02d}_part_{j:02d}.txt"
            part_text = part.strip()
            if heading:
                payload_text = f"{heading}\n\n{part_text}\n"
            else:
                payload_text = f"{part_text}\n"
            write(outdir / fn, payload_text)
            index.append(f"{fn}\tchars={len(payload_text)}")

    write(outdir / "_INDEX.tsv", "\n".join(index))
    return len(index)


def _process_language_from_active_merge(book: str, lang: str, parts: int) -> int:
    book_code = normalize_book_code(book)
    root = Path("data/translated") / book_code / lang
    merge = resolve_active_or_latest(root, book_code, lang)

    if not merge or not merge.exists():
        return 0

    text = read(merge)
    chapters = split_chapters(text)
    outdir = root / "split_chapters_for_refine"
    _reset_split_dir(outdir)

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


def process_language(book: str, lang: str, parts: int) -> int:
    # Priority: split exactly from ACTIVE_MERGE content.
    count = _process_language_from_active_merge(book, lang, parts)
    if count > 0:
        return count

    # Fallback only when active merge is missing/unavailable.
    return _process_language_from_manifest(book, lang, parts)


def main() -> None:
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
        raise RuntimeError("No canonical translate merge artifacts found")

    print(f"[OK] split_for_refine completed: {total} files")


if __name__ == "__main__":
    main()
