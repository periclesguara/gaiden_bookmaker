from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gaiden import chunker
from gaiden.chunk_contract import (
    ALLOWED_LANG,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TARGET_TOKENS,
    detect_heading,
)
from gaiden.chunk_manifest import build_manifest_v2, now_iso, sha256_text, write_manifest
from gaiden.chunk_checks import run_checks


@dataclass
class Chapter:
    chapter_id: int
    heading_line: str
    heading_number: int | None
    heading_title: str
    start_line_idx: int
    end_line_idx: int


@dataclass
class Chunk:
    chapter_id: int
    idx: int
    text: str
    start_line_idx: int
    end_line_idx: int
    token_estimate: int
    char_count: int
    sha256: str


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def normalize_book_code(value: str) -> str:
    raw = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
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


def resolve_normalized_path(book_code: str, base_dir: Path | None = None) -> Path:
    base = base_dir or (_project_root() / "data")
    return base / "normalized" / book_code / "en" / f"{book_code}_en_v2.txt"


def resolve_chunks_dir(book_code: str, base_dir: Path | None = None) -> Path:
    base = base_dir or (_project_root() / "data")
    return base / "chunks" / book_code / "en"


def resolve_manifest_path(book_code: str, base_dir: Path | None = None) -> Path:
    return resolve_chunks_dir(book_code, base_dir) / "chunks_manifest.json"


def resolve_run_report_path(book_code: str, base_dir: Path | None = None) -> Path:
    return resolve_chunks_dir(book_code, base_dir) / "chunk_run_report.json"


def _token_estimator_name() -> str:
    enc = chunker._get_tiktoken_encoder()
    return "tiktoken" if enc is not None else "chars4"


def _estimate_tokens(text: str) -> int:
    return chunker.count_tokens(text)


def _line_is_blank(line: str) -> bool:
    return line.strip() == ""


def split_headings(lines: list[str]) -> list[Chapter]:
    headings: list[tuple[int, Any]] = []
    i = 0
    while i < len(lines):
        match = detect_heading(lines, i)
        if match:
            headings.append((i, match))
            i += match.consumed_lines
            continue
        i += 1

    if not headings:
        return [
            Chapter(
                chapter_id=1,
                heading_line="",
                heading_number=None,
                heading_title="",
                start_line_idx=0,
                end_line_idx=max(0, len(lines) - 1),
            )
        ]

    chapters: list[Chapter] = []
    for idx, (line_idx, match) in enumerate(headings, start=1):
        start_line = line_idx
        end_line = (headings[idx][0] - 1) if idx < len(headings) else len(lines) - 1
        chapters.append(
            Chapter(
                chapter_id=idx,
                heading_line=match.heading_line,
                heading_number=match.heading_number,
                heading_title=match.heading_title,
                start_line_idx=start_line,
                end_line_idx=end_line,
            )
        )

    # Attach preamble to first chapter if it exists.
    if chapters[0].start_line_idx > 0:
        chapters[0].start_line_idx = 0

    return chapters


def _paragraph_spans(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    i = start
    while i <= end:
        blank_start = None
        while i <= end and _line_is_blank(lines[i]):
            if blank_start is None:
                blank_start = i
            i += 1
        if i > end:
            if blank_start is not None:
                if spans:
                    s, _ = spans[-1]
                    spans[-1] = (s, end)
                else:
                    spans.append((start, end))
            break
        p_start = blank_start if blank_start is not None else i
        while i <= end and not _line_is_blank(lines[i]):
            i += 1
        while i <= end and _line_is_blank(lines[i]):
            i += 1
        p_end = i - 1
        spans.append((p_start, p_end))
    return spans


def _split_units_by_tokens(units: list[str], joiner: str, max_tokens: int) -> list[str] | None:
    out: list[str] = []
    buf: list[str] = []
    for unit in units:
        candidate = joiner.join(buf + [unit]) if buf else unit
        if _estimate_tokens(candidate) <= max_tokens:
            buf.append(unit)
            continue
        if buf:
            out.append(joiner.join(buf))
            buf = [unit]
            continue
        # single unit too large for this strategy
        return None
    if buf:
        out.append(joiner.join(buf))
    return out


def _hard_split(text: str, max_tokens: int) -> list[str]:
    max_chars = max_tokens * 4
    out: list[str] = []
    start = 0
    while start < len(text):
        out.append(text[start : start + max_chars])
        start += max_chars
    return [p for p in out if p]


def _split_long_line(line: str, max_tokens: int) -> list[str]:
    if _estimate_tokens(line) <= max_tokens:
        return [line]
    # sentence boundary
    sentences = re.split(r"(?<=[\.!\?])\s+", line.strip())
    if len(sentences) > 1:
        chunks = _split_units_by_tokens(sentences, " ", max_tokens)
        if chunks:
            return chunks
    # hard split
    return _hard_split(line, max_tokens)


def _split_overlong_paragraph(lines: list[str], start: int, end: int, max_tokens: int) -> list[tuple[str, int, int]]:
    para_lines = lines[start : end + 1]
    para_text = "\n".join(para_lines)
    if _estimate_tokens(para_text) <= max_tokens:
        return [(para_text, start, end)]

    # Split by lines first.
    line_chunks: list[tuple[str, int, int]] = []
    buf_lines: list[str] = []
    buf_start = None
    for idx, line in enumerate(para_lines):
        abs_idx = start + idx
        if not buf_lines:
            buf_start = abs_idx
        candidate = "\n".join(buf_lines + [line]) if buf_lines else line
        if _estimate_tokens(candidate) <= max_tokens:
            buf_lines.append(line)
            continue
        if buf_lines:
            line_chunks.append(("\n".join(buf_lines), buf_start if buf_start is not None else abs_idx, abs_idx - 1))
            buf_lines = [line]
            buf_start = abs_idx
            continue
        # single line too large, split line
        for part in _split_long_line(line, max_tokens):
            line_chunks.append((part, abs_idx, abs_idx))
        buf_lines = []
        buf_start = None

    if buf_lines:
        line_chunks.append(("\n".join(buf_lines), buf_start if buf_start is not None else end, end))

    return line_chunks


def chunk_chapter(
    *,
    lines: list[str],
    chapter: Chapter,
    target_tokens: int,
    max_tokens: int,
) -> list[Chunk]:
    spans = _paragraph_spans(lines, chapter.start_line_idx, chapter.end_line_idx)
    if not spans:
        text = "\n".join(lines[chapter.start_line_idx : chapter.end_line_idx + 1])
        tok = _estimate_tokens(text)
        return [
            Chunk(
                chapter_id=chapter.chapter_id,
                idx=1,
                text=text,
                start_line_idx=chapter.start_line_idx,
                end_line_idx=chapter.end_line_idx,
                token_estimate=tok,
                char_count=len(text),
                sha256=sha256_text(text),
            )
        ]

    chunks: list[Chunk] = []
    buf_start = None
    buf_end = None
    buf_texts: list[str] = []

    def flush() -> None:
        nonlocal buf_start, buf_end, buf_texts
        if not buf_texts or buf_start is None or buf_end is None:
            return
        text = "\n".join(lines[buf_start : buf_end + 1])
        tok = _estimate_tokens(text)
        chunks.append(
            Chunk(
                chapter_id=chapter.chapter_id,
                idx=len(chunks) + 1,
                text=text,
                start_line_idx=buf_start,
                end_line_idx=buf_end,
                token_estimate=tok,
                char_count=len(text),
                sha256=sha256_text(text),
            )
        )
        buf_start = None
        buf_end = None
        buf_texts = []

    for span_start, span_end in spans:
        span_text = "\n".join(lines[span_start : span_end + 1])
        span_tokens = _estimate_tokens(span_text)

        if span_tokens > max_tokens:
            flush()
            for part_text, part_start, part_end in _split_overlong_paragraph(lines, span_start, span_end, max_tokens):
                tok = _estimate_tokens(part_text)
                chunks.append(
                    Chunk(
                        chapter_id=chapter.chapter_id,
                        idx=len(chunks) + 1,
                        text=part_text,
                        start_line_idx=part_start,
                        end_line_idx=part_end,
                        token_estimate=tok,
                        char_count=len(part_text),
                        sha256=sha256_text(part_text),
                    )
                )
            continue

        candidate_start = span_start if buf_start is None else buf_start
        candidate_end = span_end if buf_end is None else span_end
        candidate_text = "\n".join(lines[candidate_start : candidate_end + 1])
        candidate_tokens = _estimate_tokens(candidate_text)

        if candidate_tokens <= max_tokens:
            buf_start = candidate_start
            buf_end = candidate_end
            buf_texts.append(span_text)
            if candidate_tokens >= target_tokens:
                flush()
            continue

        flush()
        buf_start = span_start
        buf_end = span_end
        buf_texts = [span_text]

    flush()
    return chunks


def _git_short_sha(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def run_chunking(
    *,
    book_code: str,
    lang: str,
    normalized_path: Path,
    out_dir: Path,
    target_tokens: int,
    max_tokens: int,
    dry_run: bool,
) -> dict[str, Any]:
    if lang != ALLOWED_LANG:
        raise ValueError("Chunking é EN-only e compartilhado entre línguas destino.")

    normalized_text = normalized_path.read_text(encoding="utf-8", errors="replace")
    normalized_text = normalized_text.replace("\r\n", "\n").replace("\r", "\n")
    normalized_lines = normalized_text.splitlines()

    chapters = split_headings(normalized_lines)
    headings_detected_count = sum(1 for ch in chapters if ch.heading_line)
    single_chapter_mode = headings_detected_count == 0

    chunks_by_chapter: list[dict[str, Any]] = []
    all_chunks: list[Chunk] = []

    for chapter in chapters:
        ch_chunks = chunk_chapter(
            lines=normalized_lines,
            chapter=chapter,
            target_tokens=target_tokens,
            max_tokens=max_tokens,
        )
        for c in ch_chunks:
            all_chunks.append(c)
        chunks_by_chapter.append({
            "chapter": chapter,
            "chunks": ch_chunks,
        })

    out_dir.mkdir(parents=True, exist_ok=True)

    # Write chunks
    for entry in chunks_by_chapter:
        chapter = entry["chapter"]
        chunks = entry["chunks"]
        for idx, c in enumerate(chunks, start=1):
            filename = f"ch_{chapter.chapter_id:03d}_chunk_{idx:03d}.txt"
            file_path = out_dir / filename
            if not dry_run:
                file_path.write_text(c.text + "\n", encoding="utf-8")
            c.idx = idx

    normalized_sha256 = sha256_text(normalized_text)
    created_at = now_iso()
    chunker_version = _git_short_sha(_project_root())
    estimator = _token_estimator_name()

    chapters_manifest: list[dict[str, Any]] = []
    for entry in chunks_by_chapter:
        chapter = entry["chapter"]
        chunks = entry["chunks"]
        chunk_entries: list[dict[str, Any]] = []
        for c in chunks:
            filename = f"ch_{chapter.chapter_id:03d}_chunk_{c.idx:03d}.txt"
            file_path = filename
            chunk_id = f"{book_code}__{lang}__c{chapter.chapter_id:03d}__{c.idx:03d}"
            chunk_entries.append(
                {
                    "chunk_id": chunk_id,
                    "file_path": file_path,
                    "sha256": c.sha256,
                    "token_estimate": c.token_estimate,
                    "char_count": c.char_count,
                    "start_line_idx": c.start_line_idx,
                    "end_line_idx": c.end_line_idx,
                    "chapter_id": chapter.chapter_id,
                }
            )

        chapters_manifest.append(
            {
                "chapter_id": chapter.chapter_id,
                "heading_line": chapter.heading_line,
                "heading_number": chapter.heading_number,
                "heading_title": chapter.heading_title,
                "start_line_idx": chapter.start_line_idx,
                "end_line_idx": chapter.end_line_idx,
                "chunk_count": len(chunk_entries),
                "chunks": chunk_entries,
            }
        )

    config = {
        "target_tokens": target_tokens,
        "max_tokens": max_tokens,
        "estimator": estimator,
    }

    manifest = build_manifest_v2(
        book_code=book_code,
        lang=lang,
        normalized_path=normalized_path,
        normalized_sha256=normalized_sha256,
        chunker_version=chunker_version,
        created_at=created_at,
        config=config,
        headings_detected_count=headings_detected_count,
        single_chapter_mode=single_chapter_mode,
        chapters=chapters_manifest,
    )

    checks = run_checks(
        normalized_path=normalized_path,
        normalized_text=normalized_text,
        normalized_lines=normalized_lines,
        manifest=manifest,
        max_tokens=max_tokens,
    )

    # Compatibility fields for runner/UI logging.
    manifest["check_ok"] = checks.get("check_ok")
    manifest["check_fail_reasons"] = checks.get("failures", [])
    manifest["check_warnings"] = checks.get("warnings", [])

    report = {
        "cwd": str(Path.cwd()),
        "base_data_dir": str(_project_root() / "data"),
        "book_code": book_code,
        "lang": lang,
        "normalized_path": str(normalized_path),
        "chunks_dir": str(out_dir),
        "manifest_path": str(out_dir / "chunks_manifest.json"),
        "config": config,
        "headings_detected_count": headings_detected_count,
        "single_chapter_mode": single_chapter_mode,
        "headings": [
            {
                "chapter_id": ch.chapter_id,
                "heading_line": ch.heading_line,
                "heading_number": ch.heading_number,
                "heading_title": ch.heading_title,
                "start_line_idx": ch.start_line_idx,
                "end_line_idx": ch.end_line_idx,
            }
            for ch in chapters
        ],
        "checks": checks,
    }

    report_path = out_dir / "chunk_run_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not dry_run:
        write_manifest(out_dir / "chunks_manifest.json", manifest)

    return {"manifest": manifest, "report": report, "checks": checks}


def resolve_and_run(
    *,
    book_code: str,
    lang: str,
    normalized_path: Path | None = None,
    out_dir: Path | None = None,
    target_tokens: int | None = None,
    max_tokens: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    canonical_code = normalize_book_code(book_code)
    if lang.strip().lower() != "en":
        raise ValueError("Chunking é EN-only e compartilhado entre línguas destino.")

    base_dir = _project_root() / "data"
    normalized = normalized_path or resolve_normalized_path(canonical_code, base_dir)
    output_dir = out_dir or resolve_chunks_dir(canonical_code, base_dir)

    def _env_int(name: str, default: int) -> int:
        raw = os.getenv(name)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    tgt = target_tokens if target_tokens is not None else _env_int("GAIDEN_CHUNK_TARGET_TOKENS", DEFAULT_TARGET_TOKENS)
    mx = max_tokens if max_tokens is not None else _env_int("GAIDEN_CHUNK_MAX_TOKENS", DEFAULT_MAX_TOKENS)

    return run_chunking(
        book_code=canonical_code,
        lang="en",
        normalized_path=normalized,
        out_dir=output_dir,
        target_tokens=tgt,
        max_tokens=mx,
        dry_run=dry_run,
    )
