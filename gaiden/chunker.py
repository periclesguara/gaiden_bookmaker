from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from gaiden.chapter_agent_split import split_merged_text_into_chapters
from gaiden.structure import Unit, detect_units

CHUNKS_DIR = Path("data/chunks")

@dataclass
class Chunk:
    idx: int
    unit_type: str
    unit_title: str
    start_line: int
    end_line: int
    text: str
    est_tokens: int
    sha256: str
    out_path: str
    char_count: int

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def estimate_tokens(text: str, language: str = "en") -> int:
    """
    Heuristic. Good enough for chunk sizing.
    - English: ~4 chars/token
    - PT/ES: slightly denser => ~3.6 chars/token
    """
    chars = len(text)
    div = 4.0 if language in ("en", "eng", "english") else 3.6
    return max(1, int(chars / div))

def split_into_paragraphs(lines: List[str]) -> List[Tuple[int, int]]:
    """
    Returns list of (start_line, end_line) paragraph spans.
    Paragraph = block separated by blank lines.
    """
    spans = []
    n = len(lines)
    i = 0
    while i < n:
        while i < n and lines[i].strip() == "":
            i += 1
        if i >= n:
            break
        start = i
        while i < n and lines[i].strip() != "":
            i += 1
        end = i - 1
        spans.append((start, end))
    return spans

def make_chunks_from_text(text: str, language: str, min_tokens: int, target_tokens: int, max_tokens: int) -> List[Chunk]:
    normalized_lang = (language or "").strip().lower()
    # English literary modernization is more sensitive to overlong chunks because
    # the runtime preserves paragraph structure and cannot safely summarize.
    if normalized_lang in {"en", "eng", "english"}:
        target_tokens = min(target_tokens, 1400)
        max_tokens = min(max_tokens, 1700)

    lines = text.splitlines()
    units = detect_units(lines)

    out: List[Chunk] = []
    chunk_idx = 1

    for u in units:
        unit_lines = lines[u.start_line:u.end_line + 1]
        para_spans = split_into_paragraphs(unit_lines)

        buf_lines: List[str] = []
        buf_start_abs = None
        buf_end_abs = None

        def flush():
            nonlocal chunk_idx, buf_lines, buf_start_abs, buf_end_abs
            if not buf_lines:
                return
            chunk_text = "\n".join(buf_lines).strip() + "\n"
            tok = estimate_tokens(chunk_text, language)
            # Allow small last chunk inside unit; we'll keep it even if < min_tokens
            sha = _sha256(chunk_text)
            out_path = str(CHUNKS_DIR / "TMP")  # will be overwritten by writer
            out.append(Chunk(
                idx=chunk_idx,
                unit_type=u.unit_type,
                unit_title=u.title,
                start_line=buf_start_abs if buf_start_abs is not None else u.start_line,
                end_line=buf_end_abs if buf_end_abs is not None else u.end_line,
                text=chunk_text,
                est_tokens=tok,
                sha256=sha,
                out_path=out_path,
                char_count=len(chunk_text),
            ))
            chunk_idx += 1
            buf_lines = []
            buf_start_abs = None
            buf_end_abs = None

        def split_huge_paragraph(para_text: str, start_line: int, end_line: int) -> None:
            nonlocal chunk_idx
            sentences = re.split(r"(?<=[.!?])\s+", para_text.strip())
            acc: List[str] = []
            for s in sentences:
                if not s.strip():
                    continue
                cand = (" ".join(acc + [s])).strip()
                if estimate_tokens(cand, language) > max_tokens and acc:
                    chunk_text = (" ".join(acc)).strip() + "\n"
                    sha = _sha256(chunk_text)
                    out.append(Chunk(
                        idx=chunk_idx,
                        unit_type=u.unit_type,
                        unit_title=u.title,
                        start_line=start_line,
                        end_line=end_line,
                        text=chunk_text,
                        est_tokens=estimate_tokens(chunk_text, language),
                        sha256=sha,
                        out_path=str(CHUNKS_DIR / "TMP"),
                        char_count=len(chunk_text),
                    ))
                    chunk_idx += 1
                    acc = [s]
                else:
                    acc.append(s)
            if acc:
                chunk_text = (" ".join(acc)).strip() + "\n"
                sha = _sha256(chunk_text)
                out.append(Chunk(
                    idx=chunk_idx,
                    unit_type=u.unit_type,
                    unit_title=u.title,
                    start_line=start_line,
                    end_line=end_line,
                    text=chunk_text,
                    est_tokens=estimate_tokens(chunk_text, language),
                    sha256=sha,
                    out_path=str(CHUNKS_DIR / "TMP"),
                    char_count=len(chunk_text),
                ))
                chunk_idx += 1

        for (ps, pe) in para_spans:
            para_lines = unit_lines[ps:pe + 1]
            para_text = "\n".join(para_lines).strip() + "\n"
            if not para_text.strip():
                continue

            if not buf_lines and estimate_tokens(para_text, language) > max_tokens:
                split_huge_paragraph(para_text, u.start_line + ps, u.start_line + pe)
                continue

            candidate_lines = (buf_lines + [""] + para_lines) if buf_lines else para_lines
            candidate_text = "\n".join(candidate_lines).strip() + "\n"
            candidate_tokens = estimate_tokens(candidate_text, language)

            # If adding this paragraph would exceed max_tokens, flush current buffer first.
            if buf_lines and candidate_tokens > max_tokens:
                flush()
                # If paragraph itself exceeds max_tokens, we have to split inside it (rare, huge paragraph).
                if estimate_tokens(para_text, language) > max_tokens:
                    split_huge_paragraph(para_text, u.start_line + ps, u.start_line + pe)
                    continue

                # start new buffer with this paragraph
                buf_lines = para_lines
                buf_start_abs = u.start_line + ps
                buf_end_abs = u.start_line + pe
                continue

            # Otherwise safe to add
            if not buf_lines:
                buf_start_abs = u.start_line + ps
            buf_lines = candidate_lines
            buf_end_abs = u.start_line + pe

            # If we've reached target, flush (but ensure we are >= min_tokens)
            if estimate_tokens("\n".join(buf_lines).strip() + "\n", language) >= target_tokens:
                flush()

        # flush remaining at end of unit
        flush()

    return out


def _split_paragraphs(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"\n\s*\n", text.strip()) if item.strip()]


def _pack_paragraphs_with_token_cap(
    paragraphs: list[str],
    *,
    language: str,
    max_tokens: int,
) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    for paragraph in paragraphs:
        expanded_paragraphs = [paragraph]
        if estimate_tokens(paragraph, language) > max_tokens:
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", paragraph) if s.strip()]
            expanded_paragraphs = []
            sentence_buf: list[str] = []
            for sentence in sentences:
                sentence_candidate = " ".join(sentence_buf + [sentence]).strip()
                if sentence_buf and estimate_tokens(sentence_candidate, language) > max_tokens:
                    expanded_paragraphs.append(" ".join(sentence_buf).strip())
                    sentence_buf = [sentence]
                else:
                    sentence_buf.append(sentence)
            if sentence_buf:
                expanded_paragraphs.append(" ".join(sentence_buf).strip())

        for piece in expanded_paragraphs:
            candidate = ("\n\n".join(current + [piece])).strip() + "\n"
            if current and estimate_tokens(candidate, language) > max_tokens:
                chunks.append(("\n\n".join(current)).strip() + "\n")
                current = [piece]
                continue
            current.append(piece)
    if current:
        chunks.append(("\n\n".join(current)).strip() + "\n")
    return chunks


def _rebalance_to_max_parts(
    paragraphs: list[str],
    *,
    language: str,
    parts: int,
) -> list[str]:
    if parts <= 1 or len(paragraphs) <= 1:
        return [("\n\n".join(paragraphs)).strip() + "\n"] if paragraphs else []
    total_tokens = max(1, estimate_tokens("\n\n".join(paragraphs), language))
    target_tokens = total_tokens / parts
    out: list[list[str]] = []
    bucket: list[str] = []
    bucket_tokens = 0

    for idx, paragraph in enumerate(paragraphs):
        paragraph_tokens = estimate_tokens(paragraph, language)
        remaining = len(paragraphs) - idx
        remaining_buckets = parts - len(out)
        if (
            bucket
            and len(out) < parts - 1
            and bucket_tokens >= target_tokens
            and remaining >= remaining_buckets
        ):
            out.append(bucket)
            bucket = []
            bucket_tokens = 0
        bucket.append(paragraph)
        bucket_tokens += paragraph_tokens

    if bucket:
        out.append(bucket)

    while len(out) > parts:
        tail = out.pop()
        out[-1].extend(tail)
    while len(out) < parts:
        donor_idx = next((i for i, item in enumerate(out) if len(item) > 1), None)
        if donor_idx is None:
            break
        moved = out[donor_idx].pop()
        out.insert(donor_idx + 1, [moved])

    return [("\n\n".join(item)).strip() + "\n" for item in out if item]


def make_chapter_bound_chunks_from_text(
    text: str,
    language: str,
    min_tokens: int,
    target_tokens: int,
    max_tokens: int,
    *,
    max_parts_per_chapter: int = 8,
) -> List[Chunk]:
    chapters = split_merged_text_into_chapters(text)
    out: List[Chunk] = []
    chunk_idx = 1

    for chapter in chapters:
        chapter_text = (chapter.get("text") or "").strip()
        if not chapter_text:
            continue
        paragraphs = _split_paragraphs(chapter_text)
        if not paragraphs:
            continue

        parts = _rebalance_to_max_parts(
            paragraphs,
            language=language,
            parts=2,
        )
        for candidate_parts in (2, 4, 6, 8):
            candidate = _rebalance_to_max_parts(
                paragraphs,
                language=language,
                parts=candidate_parts,
            )
            if candidate and all(estimate_tokens(item, language) <= max_tokens for item in candidate):
                parts = candidate
                break
            parts = candidate

        # If even 8-way balancing still leaves an oversized part (usually a very
        # dense single paragraph), force a token-capped split to keep the chapter
        # boundary intact.
        if any(estimate_tokens(item, language) > max_tokens for item in parts):
            parts = _pack_paragraphs_with_token_cap(
                paragraphs,
                language=language,
                max_tokens=max_tokens,
            )

        for part_text in parts:
            normalized = part_text.strip() + "\n"
            out.append(
                Chunk(
                    idx=chunk_idx,
                    unit_type="chapter",
                    unit_title=str(chapter.get("heading") or f"Chapter {chapter.get('index', 0)}"),
                    start_line=0,
                    end_line=0,
                    text=normalized,
                    est_tokens=estimate_tokens(normalized, language),
                    sha256=_sha256(normalized),
                    out_path=str(CHUNKS_DIR / "TMP"),
                    char_count=len(normalized),
                )
            )
            chunk_idx += 1
    return out

def write_chunks(book_id: int, stage: str, chunks: List[Chunk]) -> List[Chunk]:
    out_dir = CHUNKS_DIR / f"book_{book_id:04d}" / stage
    out_dir.mkdir(parents=True, exist_ok=True)
    for c in chunks:
        p = out_dir / f"{c.idx:04d}.txt"
        p.write_text(c.text, encoding="utf-8")
        c.out_path = str(p)
    return chunks
