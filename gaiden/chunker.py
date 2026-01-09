from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

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

        for (ps, pe) in para_spans:
            para_lines = unit_lines[ps:pe + 1]
            para_text = "\n".join(para_lines).strip() + "\n"
            if not para_text.strip():
                continue

            candidate_lines = (buf_lines + [""] + para_lines) if buf_lines else para_lines
            candidate_text = "\n".join(candidate_lines).strip() + "\n"
            candidate_tokens = estimate_tokens(candidate_text, language)

            # If adding this paragraph would exceed max_tokens, flush current buffer first.
            if buf_lines and candidate_tokens > max_tokens:
                flush()
                # If paragraph itself exceeds max_tokens, we have to split inside it (rare, huge paragraph).
                if estimate_tokens(para_text, language) > max_tokens:
                    # Split paragraph by sentences as last resort
                    sentences = re.split(r"(?<=[.!?])\s+", para_text.strip())
                    acc = []
                    for s in sentences:
                        if not s.strip():
                            continue
                        cand = (" ".join(acc + [s])).strip()
                        if estimate_tokens(cand, language) > max_tokens and acc:
                            # flush sentence chunk
                            st = cand.replace(" ".join([s]), "").strip()
                            # simpler: flush acc
                            chunk_text = (" ".join(acc)).strip() + "\n"
                            sha = _sha256(chunk_text)
                            out.append(Chunk(
                                idx=chunk_idx,
                                unit_type=u.unit_type,
                                unit_title=u.title,
                                start_line=u.start_line + ps,
                                end_line=u.start_line + pe,
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
                            start_line=u.start_line + ps,
                            end_line=u.start_line + pe,
                            text=chunk_text,
                            est_tokens=estimate_tokens(chunk_text, language),
                            sha256=sha,
                            out_path=str(CHUNKS_DIR / "TMP"),
                            char_count=len(chunk_text),
                        ))
                        chunk_idx += 1
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

def write_chunks(book_id: int, stage: str, chunks: List[Chunk]) -> List[Chunk]:
    out_dir = CHUNKS_DIR / f"book_{book_id:04d}" / stage
    out_dir.mkdir(parents=True, exist_ok=True)
    for c in chunks:
        p = out_dir / f"{c.idx:04d}.txt"
        p.write_text(c.text, encoding="utf-8")
        c.out_path = str(p)
    return chunks
