from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


ROMAN_MAP = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

LANG_DIR_MAP = {
    "ptbr": "ptbr",
    "pt-br": "ptbr",
    "pt_br": "ptbr",
    "en": "en",
    "es": "es",
    "de": "de",
    "fr": "fr",
    "it": "it",
}

HEADING_WORDS = (
    "CHAPTER",
    "ADVENTURE",
    "PART",
    "BOOK",
    "CAPITULO",
    "CAPÍTULO",
    "CAPITOLO",
    "CHAPITRE",
    "TEIL",
    "KAPITEL",
)

MIN_TOKENS = 300
TARGET_TOKENS_DEFAULT = 1500
MAX_TOKENS_DEFAULT = 2000


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


def _normalize_lang(lang: str) -> str:
    raw = (lang or "en").strip()
    key = raw.lower()
    if key in LANG_DIR_MAP:
        return LANG_DIR_MAP[key]
    return key.replace("-", "").replace("_", "")


def _normalize_book_code(value: str) -> str:
    raw = (value or "").strip().lower()
    raw = raw.replace("-", "_").replace(" ", "_")
    if raw.isdigit():
        digits = raw
    else:
        m = re.match(r"^book_?(\d+)$", raw)
        if not m:
            raise ValueError("book_code deve seguir o padrão book_#### (ex: book_0003).")
        digits = m.group(1)
    num = int(digits)
    if num < 1 or num > 9999:
        raise ValueError("book_code deve estar entre 0001 e 9999.")
    return f"book_{num:04d}"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def canonical_chunks_dir(book_code: str, lang: str) -> Path:
    lang_code = _normalize_lang(lang)
    return _project_root() / "data" / "chunks" / book_code / lang_code


def canonical_normalized_path(book_code: str, lang: str) -> Path:
    lang_code = _normalize_lang(lang)
    filename = f"{book_code}_{lang_code}_v2.txt"
    return _project_root() / "data" / "normalized" / book_code / lang_code / filename


def _is_heading_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if re.match(r"^(\d+)\s*-\s+.+$", s):
        return True
    if re.match(r"^(\d+)\s*[\.\-]\s+.+$", s):
        return True
    if re.match(r"^[IVXLCDM]+\.\s+.+$", s, flags=re.IGNORECASE):
        return True
    if re.match(r"^#+\s+.+$", s):
        return True
    if re.match(rf"^({'|'.join(HEADING_WORDS)})\b", s, flags=re.IGNORECASE):
        return True
    return False


def _parse_heading(line: str, next_line: str | None) -> tuple[int | None, str, int] | None:
    s = line.strip()
    if not s:
        return None

    m = re.match(r"^(\d+)\s*-\s*(.+)$", s)
    if m:
        return int(m.group(1)), m.group(2).strip(), 1

    m = re.match(
        rf"^({'|'.join(HEADING_WORDS)})\s+([IVXLCDM0-9]+)\b\s*[\.\-:]?\s*(.*)$",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        num_raw = m.group(2)
        num = int(num_raw) if num_raw.isdigit() else _roman_to_int(num_raw)
        title = m.group(3).strip()
        return num, title, 1

    m = re.match(r"^(\d+)\s*[\.\-]\s+(.+)$", s)
    if m:
        return int(m.group(1)), m.group(2).strip(), 1

    m = re.match(r"^([IVXLCDM]+)\.\s+(.+)$", s, flags=re.IGNORECASE)
    if m:
        num = _roman_to_int(m.group(1))
        return num, m.group(2).strip(), 1

    if re.fullmatch(r"[IVXLCDM]+", s, flags=re.IGNORECASE) and next_line:
        num = _roman_to_int(s) or None
        title = next_line.strip()
        if title:
            return num, title, 2

    if re.match(r"^#+\s+(.+)$", s):
        title = re.sub(r"^#+\s+", "", s).strip()
        return None, title, 1

    return None


def _split_chapters(lines: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    preamble: list[str] = []
    chapters: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    buffer: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i]
        next_line = None
        j = i + 1
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        if j < len(lines):
            next_line = lines[j]

        heading = _parse_heading(line, next_line)
        consumed = heading[2] if heading else 1

        if heading:
            if current:
                current["lines"] = buffer
                chapters.append(current)
                buffer = []
            current = {
                "index": len(chapters) + 1,
                "heading": line.strip(),
                "title": heading[1],
                "number": heading[0],
            }
            buffer.append(line)
            if consumed > 1:
                buffer.append(next_line or "")
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


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[\.\!\?])\s+", text.strip())
    return [p for p in parts if p]


def _token_count(text: str) -> int:
    from gaiden import chunker

    return chunker.count_tokens(text)


def _split_by_chars(text: str, max_chars: int) -> list[str]:
    out: list[str] = []
    start = 0
    while start < len(text):
        out.append(text[start : start + max_chars].strip())
        start += max_chars
    return [p for p in out if p]


def _split_by_tokens(text: str, max_tokens: int) -> list[str]:
    max_chars = max_tokens * 4
    return _split_by_chars(text, max_chars)


def _split_long_text(text: str, max_tokens: int) -> list[str]:
    sentences = _split_sentences(text)
    if not sentences:
        return _split_by_tokens(text, max_tokens)

    chunks: list[str] = []
    cur = ""
    for sentence in sentences:
        if _token_count(sentence) > max_tokens:
            if cur:
                chunks.append(cur.strip())
                cur = ""
            chunks.extend(_split_by_tokens(sentence, max_tokens))
            continue
        candidate = f"{cur} {sentence}".strip() if cur else sentence
        if _token_count(candidate) <= max_tokens:
            cur = candidate
        else:
            chunks.append(cur.strip())
            cur = sentence
    if cur:
        chunks.append(cur.strip())
    return chunks


def _chunk_paragraphs(paras: list[str], target_tokens: int, max_tokens: int) -> list[str]:
    chunks: list[str] = []
    buf: list[str] = []
    buf_tokens = 0

    for para in paras:
        para = para.strip()
        if not para:
            continue

        para_tokens = _token_count(para)
        if not buf and para_tokens > max_tokens:
            chunks.extend(_split_long_text(para, max_tokens))
            continue

        candidate_text = "\n\n".join(buf + [para]) if buf else para
        candidate_tokens = _token_count(candidate_text)
        if candidate_tokens <= target_tokens:
            buf.append(para)
            buf_tokens = candidate_tokens
            continue

        if buf:
            chunks.append("\n\n".join(buf).strip())
        buf = []
        buf_tokens = 0

        if para_tokens > max_tokens:
            chunks.extend(_split_long_text(para, max_tokens))
        else:
            buf = [para]
            buf_tokens = para_tokens

    if buf:
        chunks.append("\n\n".join(buf).strip())

    if len(chunks) >= 2:
        last_tokens = _token_count(chunks[-1])
        threshold = int(target_tokens * 0.4)
        if last_tokens < threshold:
            merged = chunks[-2].rstrip() + "\n\n" + chunks[-1].lstrip()
            if _token_count(merged) <= max_tokens:
                chunks[-2] = merged
                chunks.pop()

    if len(chunks) >= 2:
        idx = 0
        while idx < len(chunks):
            tok = _token_count(chunks[idx])
            if tok >= MIN_TOKENS or len(chunks) == 1:
                idx += 1
                continue
            if idx > 0:
                merged = chunks[idx - 1].rstrip() + "\n\n" + chunks[idx].lstrip()
                if _token_count(merged) <= max_tokens:
                    chunks[idx - 1] = merged
                    chunks.pop(idx)
                    continue
            if idx + 1 < len(chunks):
                merged = chunks[idx].rstrip() + "\n\n" + chunks[idx + 1].lstrip()
                if _token_count(merged) <= max_tokens:
                    chunks[idx] = merged
                    chunks.pop(idx + 1)
                    continue
            idx += 1

    return chunks


def _chunk_has_cross_chapter(chunk_text: str) -> bool:
    lines = chunk_text.splitlines()
    first_nonempty = None
    heading_indices: list[int] = []
    for idx, line in enumerate(lines):
        if line.strip() and first_nonempty is None:
            first_nonempty = idx
        if _is_heading_line(line):
            heading_indices.append(idx)
    if not heading_indices:
        return False
    if len(heading_indices) == 1:
        return first_nonempty is not None and heading_indices[0] != first_nonempty
    return True


def chunk_book(
    book_code: str,
    lang: str,
    normalized_path: Path,
    out_dir: Path | None = None,
    target_tokens: int = TARGET_TOKENS_DEFAULT,
    max_tokens: int = MAX_TOKENS_DEFAULT,
) -> dict[str, Any]:
    canonical_code = _normalize_book_code(book_code)
    lang_lower = _normalize_lang(lang)

    if lang_lower != "en":
        raise ValueError("Chunking is EN-only; shared chunks live under /en.")

    if not normalized_path.exists():
        raise FileNotFoundError(f"Normalized não encontrado: {normalized_path}")

    raw = normalized_path.read_text(encoding="utf-8", errors="replace")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.splitlines()

    _, chapters = _split_chapters(lines)
    if not chapters:
        raise ValueError("INVALID_STATE: nenhum capítulo detectado no texto normalizado.")

    out_dir = out_dir or canonical_chunks_dir(canonical_code, lang)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "book_code": canonical_code,
        "language": lang_lower,
        "normalized_path": str(normalized_path),
        "out_dir": str(out_dir),
        "chapter_count": 0,
        "chunk_count": 0,
        "total_chunks": 0,
        "target_tokens": target_tokens,
        "max_tokens": max_tokens,
        "target_chars_estimate": target_tokens * 4,
        "max_chars_estimate": max_tokens * 4,
        "per_chapter": [],
    }

    def write_chapter(chapter_id: int, title: str, lines: list[str]) -> None:
        paras = _split_paragraphs(lines)
        chunks = _chunk_paragraphs(paras, target_tokens, max_tokens)
        chunk_files: list[str] = []
        char_counts: list[int] = []
        token_counts: list[int] = []
        for idx, chunk_text in enumerate(chunks, start=1):
            if not chunk_text.strip():
                continue
            filename = f"ch_{chapter_id:02d}_chunk_{idx:03d}.txt"
            out_path = out_dir / filename
            out_path.write_text(chunk_text + "\n", encoding="utf-8")
            chunk_files.append(filename)
            char_counts.append(len(chunk_text))
            token_counts.append(_token_count(chunk_text))
            manifest["chunk_count"] += 1
        manifest["per_chapter"].append(
            {
                "chapter_id": chapter_id,
                "title": title,
                "chunk_files": chunk_files,
                "char_counts": char_counts,
                "token_counts": token_counts,
            }
        )

    for chapter in chapters:
        chap_num = chapter.get("number")
        if not chap_num:
            chap_num = chapter["index"]
        write_chapter(int(chap_num), chapter.get("title", ""), chapter.get("lines", []))

    manifest["chapter_count"] = len(manifest["per_chapter"])
    manifest["total_chunks"] = manifest["chunk_count"]

    check_ok = True
    check_reasons: list[str] = []
    oversize = []
    cross = []
    for entry in manifest["per_chapter"]:
        if not entry["chunk_files"]:
            check_ok = False
            check_reasons.append(f"capítulo sem chunks: {entry.get('chapter_id')}")
        for file_name, tok in zip(entry["chunk_files"], entry["token_counts"]):
            if tok > max_tokens:
                oversize.append(file_name)
    for entry in manifest["per_chapter"]:
        for file_name in entry["chunk_files"]:
            chunk_text = (out_dir / file_name).read_text(encoding="utf-8", errors="replace")
            if _chunk_has_cross_chapter(chunk_text):
                cross.append(file_name)

    if oversize:
        check_ok = False
        check_reasons.append(f"chunks acima do max_tokens: {', '.join(sorted(set(oversize))[:5])}")
    if cross:
        check_ok = False
        check_reasons.append(f"heading detectado dentro do chunk: {', '.join(sorted(set(cross))[:5])}")

    manifest["check_ok"] = check_ok
    manifest["check_fail_reasons"] = check_reasons

    manifest_path = out_dir / "chunks_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chunk normalized book into per-chapter chunks.")
    parser.add_argument("book_code", nargs="?", help="book_code (book_0003)")
    parser.add_argument("language", nargs="?", help="Language code (EN, PT-BR, ES, DE, FR, IT)")
    parser.add_argument("--book", help="book_code (book_0003)")
    parser.add_argument("--lang", help="Language code (EN, PT-BR, ES, DE, FR, IT)")
    parser.add_argument("--normalized", help="Path to normalized file")
    parser.add_argument("--out", required=False, help="Output directory (optional)")
    parser.add_argument("--target-tokens", type=int, default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--target-chars", type=int, default=None, help="Deprecated. Use --target-tokens.")
    parser.add_argument("--max-chars", type=int, default=None, help="Deprecated. Use --max-tokens.")
    args = parser.parse_args(argv)

    book_code = args.book or args.book_code
    lang = args.lang or args.language
    if not book_code or not lang:
        raise SystemExit("book_code e language são obrigatórios.")

    out_dir = Path(args.out) if args.out else None
    normalized_path = Path(args.normalized) if args.normalized else canonical_normalized_path(book_code, lang)
    def _env_int(name: str, default: int) -> int:
        raw = os.getenv(name)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    target_tokens = args.target_tokens if args.target_tokens is not None else _env_int("GAIDEN_CHUNK_TARGET_TOKENS", TARGET_TOKENS_DEFAULT)
    max_tokens = args.max_tokens if args.max_tokens is not None else _env_int("GAIDEN_CHUNK_MAX_TOKENS", MAX_TOKENS_DEFAULT)
    if args.target_tokens is None and args.target_chars is not None:
        target_tokens = max(1, int(round(args.target_chars / 4)))
    if args.max_tokens is None and args.max_chars is not None:
        max_tokens = max(1, int(round(args.max_chars / 4)))

    manifest = chunk_book(
        book_code,
        lang,
        normalized_path,
        out_dir=out_dir,
        target_tokens=target_tokens,
        max_tokens=max_tokens,
    )
    status = "OK" if manifest.get("check_ok") else "FAIL"
    print(f"[{status}] chunk_check")
    if manifest.get("check_fail_reasons"):
        for reason in manifest["check_fail_reasons"]:
            print(f"[FAIL] {reason}")
    print(
        f"[OUTPUT] chapters={manifest.get('chapter_count', 0)} "
        f"chunks={manifest.get('chunk_count', 0)} "
        f"manifest={Path(manifest['out_dir']) / 'chunks_manifest.json'}"
    )
    return 0 if manifest.get("check_ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
