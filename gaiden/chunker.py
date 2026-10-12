from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Tuple

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
    token_count: int = 0
    tokenizer_name: str = "character-estimate:v1"

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

@dataclass(frozen=True)
class _Piece:
    text: str
    start_line: int
    end_line: int
    separator: str = "\n\n"


def make_chunks_from_text(
    text: str,
    language: str,
    min_tokens: int,
    target_tokens: int,
    max_tokens: int,
    *,
    token_counter: Callable[[str], int] | None = None,
    token_splitter: Callable[[str, int], list[str]] | None = None,
    tokenizer_name: str = "character-estimate:v1",
) -> List[Chunk]:
    """Split text without crossing detected structural units.

    Author Studio supplies a real tokenizer. Other callers retain the explicit
    character-estimate compatibility path.
    """
    normalized_lang = (language or "").strip().lower()
    count = token_counter or (lambda value: estimate_tokens(value, normalized_lang))

    def fallback_split(value: str, limit: int) -> list[str]:
        if not value:
            return []
        approximate_chars = max(1, int(limit * (4.0 if normalized_lang in {"en", "eng", "english"} else 3.6)))
        return [value[offset : offset + approximate_chars] for offset in range(0, len(value), approximate_chars)]

    split_tokens = token_splitter or fallback_split
    lines = text.splitlines()
    units = detect_units(lines)
    out: List[Chunk] = []
    chunk_idx = 1

    def normalize(value: str) -> str:
        return value.strip() + "\n"

    def expanded_paragraphs(unit: Unit) -> list[_Piece]:
        unit_lines = lines[unit.start_line : unit.end_line + 1]
        pieces: list[_Piece] = []
        for paragraph_start, paragraph_end in split_into_paragraphs(unit_lines):
            start_line = unit.start_line + paragraph_start
            end_line = unit.start_line + paragraph_end
            paragraph = "\n".join(unit_lines[paragraph_start : paragraph_end + 1]).strip()
            if not paragraph:
                continue
            if count(normalize(paragraph)) <= max_tokens:
                pieces.append(_Piece(paragraph, start_line, end_line))
                continue

            sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", paragraph) if item.strip()]
            if not sentences:
                sentences = [paragraph]
            first_sentence_piece = True
            for sentence in sentences:
                sentence_parts = [sentence]
                if count(normalize(sentence)) > max_tokens:
                    split_limit = max(1, max_tokens - 1)
                    while True:
                        sentence_parts = [part for part in split_tokens(sentence, split_limit) if part]
                        largest = max((count(normalize(part)) for part in sentence_parts), default=0)
                        if largest <= max_tokens:
                            break
                        split_limit -= max(1, largest - max_tokens + 1)
                        if split_limit < 1:
                            raise ValueError("Não foi possível respeitar o limite rígido por tokens.")
                for part_index, part in enumerate(sentence_parts):
                    if count(normalize(part)) > max_tokens:
                        raise ValueError("O divisor por tokens produziu conteúdo acima do limite rígido.")
                    separator = "\n\n" if first_sentence_piece else ("" if part_index else " ")
                    pieces.append(_Piece(part, start_line, end_line, separator))
                    first_sentence_piece = False
        return pieces

    for unit in units:
        unit_chunks: list[tuple[str, int, int]] = []
        current = ""
        current_start = unit.start_line
        current_end = unit.start_line

        def flush() -> None:
            nonlocal current, current_start, current_end
            normalized = normalize(current) if current.strip() else ""
            if normalized:
                unit_chunks.append((normalized, current_start, current_end))
            current = ""

        for piece in expanded_paragraphs(unit):
            candidate = piece.text if not current else current + piece.separator + piece.text
            if current and count(normalize(candidate)) > max_tokens:
                flush()
                candidate = piece.text
            if count(normalize(candidate)) > max_tokens:
                raise ValueError("Chunk candidato acima do limite rígido de tokens.")
            if not current:
                current_start = piece.start_line
            current = candidate
            current_end = piece.end_line
            if count(normalize(current)) >= target_tokens:
                flush()
        flush()

        index = 0
        while index < len(unit_chunks):
            chunk_text, start_line, end_line = unit_chunks[index]
            if count(chunk_text) >= 100 or len(unit_chunks) == 1:
                index += 1
                continue
            if index > 0:
                previous_text, previous_start, _ = unit_chunks[index - 1]
                merged = normalize(previous_text.rstrip() + "\n\n" + chunk_text.lstrip())
                if count(merged) <= max_tokens:
                    unit_chunks[index - 1] = (merged, previous_start, end_line)
                    unit_chunks.pop(index)
                    continue
            if index + 1 < len(unit_chunks):
                next_text, _, next_end = unit_chunks[index + 1]
                merged = normalize(chunk_text.rstrip() + "\n\n" + next_text.lstrip())
                if count(merged) <= max_tokens:
                    unit_chunks[index] = (merged, start_line, next_end)
                    unit_chunks.pop(index + 1)
                    continue
            index += 1

        if len(unit_chunks) > 1 and count(unit_chunks[-1][0]) < min_tokens:
            previous_text, previous_start, _ = unit_chunks[-2]
            tail_text, _, tail_end = unit_chunks[-1]
            combined = normalize(previous_text.rstrip() + "\n\n" + tail_text.lstrip())
            combined_tokens = count(combined)
            if combined_tokens <= max_tokens:
                unit_chunks[-2:] = [(combined, previous_start, tail_end)]
            elif combined_tokens >= min_tokens * 2:
                balanced_limit = min(max_tokens - 4, max(1, (combined_tokens + 1) // 2))
                balanced = [normalize(part) for part in split_tokens(combined.strip(), balanced_limit) if part.strip()]
                if (
                    len(balanced) >= 2
                    and all(min_tokens <= count(part) <= max_tokens for part in balanced)
                ):
                    unit_chunks[-2:] = [
                        (part, previous_start, tail_end) for part in balanced
                    ]

        for chunk_text, start_line, end_line in unit_chunks:
            real_tokens = count(chunk_text)
            if not chunk_text.strip():
                raise ValueError("O splitter produziu um chunk vazio.")
            if real_tokens > max_tokens:
                raise ValueError(f"Chunk com {real_tokens} tokens excede o limite {max_tokens}.")
            out.append(
                Chunk(
                    idx=chunk_idx,
                    unit_type=unit.unit_type,
                    unit_title=unit.title,
                    start_line=start_line,
                    end_line=end_line,
                    text=chunk_text,
                    est_tokens=estimate_tokens(chunk_text, normalized_lang),
                    sha256=_sha256(chunk_text),
                    out_path=str(CHUNKS_DIR / "TMP"),
                    char_count=len(chunk_text),
                    token_count=real_tokens,
                    tokenizer_name=tokenizer_name,
                )
            )
            chunk_idx += 1

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
