from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

SUPPORTED_SUFFIXES = {".md", ".txt"}
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.+?)\s*$")


@dataclass(frozen=True)
class SourceChunk:
    chunk_id: str
    source_path: str
    source_sha256: str
    ordinal: int
    heading: str
    text: str
    text_sha256: str


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def discover_sources(root: Path) -> list[Path]:
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"source root is not a directory: {root}")
    sources: list[Path] = []
    for path in sorted(root.rglob("*")):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.is_symlink():
            raise ValueError(f"symlink sources are forbidden: {path}")
        if path.is_file() and path.suffix.casefold() in SUPPORTED_SUFFIXES:
            sources.append(path)
    if not sources:
        raise ValueError(f"no .txt or .md sources found under {root}")
    return sources


def _paragraphs(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if "\x00" in normalized:
        raise ValueError("NUL byte found in text source")
    return [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]


def _split_oversized(text: str, max_chars: int) -> Iterator[str]:
    remaining = text.strip()
    while len(remaining) > max_chars:
        cut = remaining.rfind(" ", 0, max_chars + 1)
        if cut < max_chars // 2:
            cut = max_chars
        yield remaining[:cut].strip()
        remaining = remaining[cut:].strip()
    if remaining:
        yield remaining


def chunk_source(
    path: Path,
    root: Path,
    *,
    target_chars: int = 3200,
    max_chars: int = 4800,
) -> list[SourceChunk]:
    if target_chars < 500 or max_chars < target_chars:
        raise ValueError("invalid chunk size")
    raw = path.read_text(encoding="utf-8", errors="strict")
    source_sha = _sha256(raw)
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    units: list[tuple[str, str]] = []
    heading = path.stem.replace("_", " ")
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if buffer:
            units.append((heading, "\n\n".join(buffer).strip()))
            buffer = []

    for paragraph in _paragraphs(raw):
        match = HEADING.match(paragraph)
        if match:
            flush()
            heading = match.group("title").strip()
            continue
        for piece in _split_oversized(paragraph, max_chars):
            candidate = "\n\n".join([*buffer, piece])
            if buffer and len(candidate) > max_chars:
                flush()
            buffer.append(piece)
            if len("\n\n".join(buffer)) >= target_chars:
                flush()
    flush()

    chunks: list[SourceChunk] = []
    for ordinal, (unit_heading, text) in enumerate(units, start=1):
        text_sha = _sha256(text)
        identity = _sha256(f"{relative}\0{ordinal}\0{text_sha}")
        chunks.append(
            SourceChunk(
                chunk_id=identity,
                source_path=relative,
                source_sha256=source_sha,
                ordinal=ordinal,
                heading=unit_heading,
                text=text,
                text_sha256=text_sha,
            )
        )
    return chunks


def load_corpus(root: Path) -> tuple[list[Path], list[SourceChunk]]:
    resolved = root.expanduser().resolve(strict=True)
    sources = discover_sources(resolved)
    chunks = [chunk for path in sources for chunk in chunk_source(path, resolved)]
    accounted = {chunk.source_path for chunk in chunks}
    expected = {path.relative_to(resolved).as_posix() for path in sources}
    if accounted != expected:
        missing = sorted(expected - accounted)
        raise ValueError(f"partial corpus indexing; sources without chunks: {missing}")
    return sources, chunks
