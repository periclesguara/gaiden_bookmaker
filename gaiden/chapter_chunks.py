from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from gaiden import chunker

ROMAN_MAP = {
    "I": 1, "II": 2, "III": 3, "IV": 4,
    "V": 5, "VI": 6, "VII": 7, "VIII": 8,
    "IX": 9, "X": 10, "XI": 11, "XII": 12,
}

TITLE_MAP = {
    1: "A Scandal in Bohemia",
    2: "A Case of Identity",
    3: "The Red-Headed League",
    4: "The Boscombe Valley Mystery",
    5: "The Five Orange Pips",
    6: "The Man with the Twisted Lip",
    7: "The Adventure of the Blue Carbuncle",
    8: "The Adventure of the Speckled Band",
    9: "The Adventure of the Engineer's Thumb",
    10: "The Adventure of the Noble Bachelor",
    11: "The Adventure of the Beryl Coronet",
    12: "The Adventure of the Copper Beeches",
}

ROMAN_LINE_RE = re.compile(r"^\s*([IVXLCDM]+)\s*$")
CHAPTER_HEADING_RE = re.compile(r"^#\s+([IVXLCDM]+)\.\s+(.+)$")
ALT_CHAPTER_RE = re.compile(r"^(\d+)\.\s+(.+)$")

FIRST_STORY_START_RE = re.compile(r"^To Sherlock Holmes\b")

DEFAULT_MIN_TOKENS = 1500
DEFAULT_TARGET_TOKENS = 1800
DEFAULT_MAX_TOKENS = 2200


def normalize_sherlock_text(raw: str) -> str:
    lines = raw.splitlines()
    output_lines: list[str] = []

    started_narrative = False
    current_chapter: int | None = None
    sub_idx = 0

    for line in lines:
        stripped = line.strip()

        if not started_narrative:
            if FIRST_STORY_START_RE.search(line):
                current_chapter = 1
                sub_idx = 0

                output_lines.append(":: center")
                output_lines.append(f"# I. {TITLE_MAP[1]}")
                output_lines.append(":::")
                output_lines.append("")

                started_narrative = True
                output_lines.append(line)
            continue

        m = ROMAN_LINE_RE.match(stripped)
        if m:
            roman = m.group(1)
            num = ROMAN_MAP.get(roman)

            if num is None:
                output_lines.append(line)
                continue

            if num in TITLE_MAP and (current_chapter != num):
                current_chapter = num
                sub_idx = 0

                output_lines.append("")
                output_lines.append(":: center")
                output_lines.append(f"# {roman}. {TITLE_MAP[num]}")
                output_lines.append(":::")
                output_lines.append("")
                continue

            if current_chapter is not None:
                sub_idx += 1
                output_lines.append("")
                output_lines.append(f"## {sub_idx}")
                output_lines.append("")
                continue

            output_lines.append(line)
            continue

        output_lines.append(line)

    return "\n".join(output_lines)


def _extract_chapters(text: str) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    buffer: list[str] = []

    for line in text.splitlines():
        m = CHAPTER_HEADING_RE.match(line.strip())
        if m:
            if current:
                current["text"] = "\n".join(buffer).strip() + "\n"
                chapters.append(current)
                buffer = []
            roman = m.group(1)
            current = {
                "number": ROMAN_MAP.get(roman, 0),
                "roman": roman,
                "title": m.group(2).strip(),
            }
            buffer.append(line)
            continue
        if current:
            buffer.append(line)

    if current:
        current["text"] = "\n".join(buffer).strip() + "\n"
        chapters.append(current)

    return chapters


def _extract_chapters_from_numbers(text: str) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    buffer: list[str] = []

    for line in text.splitlines():
        m = ALT_CHAPTER_RE.match(line.strip())
        if m:
            if current:
                current["text"] = "\n".join(buffer).strip() + "\n"
                chapters.append(current)
                buffer = []
            num = int(m.group(1))
            current = {
                "number": num,
                "roman": "",
                "title": m.group(2).strip().title(),
            }
            buffer.append(f"# {num}. {current['title']}")
            continue
        if current:
            buffer.append(line)

    if current:
        current["text"] = "\n".join(buffer).strip() + "\n"
        chapters.append(current)

    return chapters


def build_chapter_chunks(
    raw_text: str,
    output_dir: Path,
    manifest_path: Path | None = None,
    language: str = "en",
    min_tokens: int = DEFAULT_MIN_TOKENS,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    normalized_text = normalize_sherlock_text(raw_text)
    chapters = _extract_chapters(normalized_text)
    if not chapters:
        chapters = _extract_chapters_from_numbers(normalized_text)
    if not chapters:
        raise ValueError("Nenhum capitulo detectado no texto normalizado.")

    output_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {"chapters": []}
    for chapter in chapters:
        chapter_num = chapter.get("number", 0)
        chapter_text = chapter["text"]
        chunks = chunker.make_chunks_from_text(
            chapter_text,
            language,
            min_tokens,
            target_tokens,
            max_tokens,
        )

        entries: list[dict[str, Any]] = []
        for idx, c in enumerate(chunks, start=1):
            filename = f"chunk{idx:02d}_cap_{chapter_num:02d}.txt"
            out_path = output_dir / filename
            out_path.write_text(c.text, encoding="utf-8")
            entries.append(
                {
                    "filename": filename,
                    "est_tokens": c.est_tokens,
                    "char_count": c.char_count,
                }
            )

        manifest["chapters"].append(
            {
                "number": chapter_num,
                "roman": chapter.get("roman"),
                "title": chapter.get("title"),
                "chunk_count": len(entries),
                "chunks": entries,
            }
        )

    if manifest_path:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

    return {
        "normalized_text": normalized_text,
        "manifest": manifest,
        "manifest_path": str(manifest_path) if manifest_path else "",
        "output_dir": str(output_dir),
    }
