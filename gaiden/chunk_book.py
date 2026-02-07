from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Iterable


ROMAN_MAP = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _roman_to_int(s: str) -> int | None:
    s = s.upper().strip()
    if not s or not all(c in ROMAN_MAP for c in s):
        return None
    total, prev = 0, 0
    for c in reversed(s):
        val = ROMAN_MAP[c]
        total += -val if val < prev else val
        prev = val
    return total


def _estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def _heading_match(line: str) -> tuple[str, str] | None:
    s = line.strip()
    if not s:
        return None

    m = re.match(r"^(\d+)\s*-\s*(.+)$", s)
    if m:
        return m.group(1), m.group(2).strip()

    m = re.match(
        r"^(CHAPTER|ADVENTURE|PART|BOOK)\s+([IVXLCDM0-9]+)\b\s*[\.\-:]?\s*(.*)$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        num_raw = m.group(2)
        num = str(_roman_to_int(num_raw) or num_raw)
        title = m.group(3).strip()
        return num, title

    m = re.match(r"^(\d+)\s*[\.\-]\s+(.+)$", s)
    if m:
        return m.group(1), m.group(2).strip()

    m = re.match(r"^([IVXLCDM]+)\.\s+(.+)$", s)
    if m:
        num = _roman_to_int(m.group(1))
        return str(num or m.group(1)), m.group(2).strip()

    return None


def _roman_only(line: str) -> bool:
    return bool(re.fullmatch(r"[IVXLCDM]+", line.strip(), flags=re.IGNORECASE))


def _split_chapters(lines: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    preamble: list[str] = []
    chapters: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    buffer: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        heading = _heading_match(line)
        consumed = 1

        if heading is None and _roman_only(line):
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines):
                heading = (line.strip(), lines[j].strip())
                consumed = j - i + 1

        if heading:
            if current:
                current["lines"] = buffer
                chapters.append(current)
                buffer = []

            current = {
                "index": len(chapters) + 1,
                "heading_raw": line.strip(),
                "title": heading[1],
            }
            buffer.append(line)
            if consumed > 1:
                for k in range(i + 1, i + consumed):
                    buffer.append(lines[k])
            i += consumed
            continue

        if current:
            buffer.append(line)
        else:
            preamble.append(line)
        i += 1

    if current:
        current["lines"] = buffer
        chapters.append(current)

    return preamble, chapters


def _split_paragraphs(lines: list[str]) -> list[str]:
    paras: list[str] = []
    buf: list[str] = []
    for line in lines:
        if line.strip() == "":
            if buf:
                paras.append("\n".join(buf).strip())
                buf = []
            continue
        buf.append(line)
    if buf:
        paras.append("\n".join(buf).strip())
    return paras


def _split_sentence_blocks(text: str) -> list[str]:
    parts = re.split(r"(?<=[\.\!\?])\s+", text.strip())
    return [p for p in parts if p]


def _split_by_chars(text: str, max_tokens: int) -> list[str]:
    max_chars = max_tokens * 4
    out: list[str] = []
    start = 0
    while start < len(text):
        out.append(text[start : start + max_chars].strip())
        start += max_chars
    return [p for p in out if p]


def _chunk_paragraphs(paras: list[str], max_tokens: int) -> tuple[list[str], int, int]:
    chunks: list[str] = []
    oversize_splits = 0
    max_est = 0

    current = ""
    for para in paras:
        if not current:
            if _estimate_tokens(para) <= max_tokens:
                current = para
                max_est = max(max_est, _estimate_tokens(current))
                continue
            oversize_splits += 1
            pieces = []
            for sent in _split_sentence_blocks(para):
                if _estimate_tokens(sent) > max_tokens:
                    pieces.extend(_split_by_chars(sent, max_tokens))
                else:
                    pieces.append(sent)
            cur = ""
            for piece in pieces:
                if not cur:
                    cur = piece
                    continue
                cand = f"{cur} {piece}"
                if _estimate_tokens(cand) <= max_tokens:
                    cur = cand
                else:
                    chunks.append(cur.strip())
                    max_est = max(max_est, _estimate_tokens(cur))
                    cur = piece
            if cur:
                chunks.append(cur.strip())
                max_est = max(max_est, _estimate_tokens(cur))
            current = ""
            continue

        candidate = f"{current}\n\n{para}"
        if _estimate_tokens(candidate) <= max_tokens:
            current = candidate
            max_est = max(max_est, _estimate_tokens(current))
        else:
            chunks.append(current.strip())
            max_est = max(max_est, _estimate_tokens(current))
            current = ""
            if _estimate_tokens(para) <= max_tokens:
                current = para
                max_est = max(max_est, _estimate_tokens(current))
            else:
                oversize_splits += 1
                pieces = []
                for sent in _split_sentence_blocks(para):
                    if _estimate_tokens(sent) > max_tokens:
                        pieces.extend(_split_by_chars(sent, max_tokens))
                    else:
                        pieces.append(sent)
                cur = ""
                for piece in pieces:
                    if not cur:
                        cur = piece
                        continue
                    cand = f"{cur} {piece}"
                    if _estimate_tokens(cand) <= max_tokens:
                        cur = cand
                    else:
                        chunks.append(cur.strip())
                        max_est = max(max_est, _estimate_tokens(cur))
                        cur = piece
                if cur:
                    chunks.append(cur.strip())
                    max_est = max(max_est, _estimate_tokens(cur))
                current = ""

    if current:
        chunks.append(current.strip())
        max_est = max(max_est, _estimate_tokens(current))

    return chunks, oversize_splits, max_est


def _write_chunks(
    out_dir: Path,
    prefix: str,
    chunks: Iterable[str],
    alias_chunk: bool = True,
) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for idx, text in enumerate(chunks, start=1):
        canonical = f"{prefix}__p_{idx:02d}.txt"
        canonical_path = out_dir / canonical
        canonical_path.write_text(text + "\n", encoding="utf-8")

        translate_alias = f"{prefix}_chunk_{idx:03d}.txt" if alias_chunk else ""
        if translate_alias:
            alias_path = out_dir / translate_alias
            alias_path.write_text(text + "\n", encoding="utf-8")

        record = {
            "file": canonical,
            "est_tokens": _estimate_tokens(text),
            "chars": len(text),
            "lines": len(text.splitlines()),
        }
        if translate_alias:
            record["translate_alias"] = translate_alias
        records.append(record)
    return records


def chunk_book_en(book_code: str, normalized_path: Path, out_dir: Path, max_tokens: int = 1500) -> dict:
    raw = normalized_path.read_text(encoding="utf-8", errors="replace")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.splitlines()

    preamble_lines, chapters = _split_chapters(lines)
    manifest: dict[str, Any] = {
        "book_code": book_code,
        "language": "en",
        "normalized_path": str(normalized_path),
        "out_dir": str(out_dir),
        "max_tokens": max_tokens,
        "chapters": [],
        "total_chunks": 0,
        "max_est_tokens": 0,
        "oversize_splits": 0,
    }

    if preamble_lines:
        paras = _split_paragraphs(preamble_lines)
        chunks, overs, max_est = _chunk_paragraphs(paras, max_tokens)
        records = _write_chunks(out_dir, "ch_00", chunks)
        manifest["chapters"].append(
            {
                "index": 0,
                "heading": "PREAMBLE",
                "title": "",
                "chunks": records,
            }
        )
        manifest["total_chunks"] += len(records)
        manifest["oversize_splits"] += overs
        manifest["max_est_tokens"] = max(manifest["max_est_tokens"], max_est)

    for chapter in chapters:
        paras = _split_paragraphs(chapter.get("lines", []))
        chunks, overs, max_est = _chunk_paragraphs(paras, max_tokens)
        prefix = f"ch_{chapter['index']:02d}"
        records = _write_chunks(out_dir, prefix, chunks)
        manifest["chapters"].append(
            {
                "index": chapter["index"],
                "heading": chapter.get("heading_raw", ""),
                "title": chapter.get("title", ""),
                "chunks": records,
            }
        )
        manifest["total_chunks"] += len(records)
        manifest["oversize_splits"] += overs
        manifest["max_est_tokens"] = max(manifest["max_est_tokens"], max_est)

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Chunk normalized EN book into deterministic parts.")
    parser.add_argument("--book", required=True, help="book_code (book_0001)")
    parser.add_argument("--normalized", required=True, help="Path to normalized EN file")
    parser.add_argument("--out", required=True, help="Output directory for chunks")
    parser.add_argument("--max-tokens", type=int, default=1500)
    args = parser.parse_args()

    manifest = chunk_book_en(
        args.book,
        Path(args.normalized),
        Path(args.out),
        max_tokens=args.max_tokens,
    )
    print(
        f"[OK] chapters={len(manifest.get('chapters', []))} "
        f"chunks={manifest.get('total_chunks', 0)} "
        f"max_est_tokens={manifest.get('max_est_tokens', 0)}"
    )
