#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
import re
from pathlib import Path

from gaiden.openai_client import call_agent_text

# Chapter heading patterns:
# - Markdown headings with numeric index (## 2. TITLE, ### 4. TITLE)
# - Bold numeric headings (**5. TITLE**)
# - Plain numeric headings (9. TITLE)
# - Roman numeral headings (I. TITLE) only if TITLE is ALL CAPS
MD_NUM_RE = re.compile(r"^#{1,3}\s+(\d+)\.\s+(.+)$")
BOLD_NUM_RE = re.compile(r"^\*\*(\d+)\.\s+(.+?)\*\*$")
PLAIN_NUM_RE = re.compile(r"^(\d+)\.\s+(.+)$")
ROMAN_RE = re.compile(r"^([IVXLCDM]+)\.\s+(.+)$")
ROMAN_ONLY_RE = re.compile(r"^([IVXLCDM]+)\.?$")
TOC_INLINE_SPLIT_RE = re.compile(r"([A-Za-z’])([IVXLCDM]+)\.\s+")
ROMAN_DOT_GLUE_RE = re.compile(r"^([IVXLCDM]+)\.(\S)")

ROMAN_MAP = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
}

SYSTEM_PROMPT = (
    "You are a precise English literary refiner.\n"
    "Task: refine style, clarity, and rhythm while preserving meaning, order, and structure.\n"
    "Rules:\n"
    "- Output ONLY the refined text. No summaries, no bullet points, no analysis.\n"
    "- Keep chapter heading as the first line (e.g., '1. Title').\n"
    "- Do not add or remove paragraphs or sentences.\n"
    "- Do not change language or format.\n"
)

MIN_OUTPUT_RATIO = 0.6
MAX_RETRIES = 2


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def _is_all_caps_title(title: str) -> bool:
    stripped = title.strip()
    if not stripped:
        return False
    return stripped == stripped.upper()


def _is_heading(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if MD_NUM_RE.match(s) or BOLD_NUM_RE.match(s) or PLAIN_NUM_RE.match(s):
        return True
    m = ROMAN_RE.match(s)
    if m and _is_all_caps_title(m.group(2)):
        return True
    if ROMAN_ONLY_RE.match(s):
        return True
    return False


def _roman_to_int(roman: str) -> int | None:
    return ROMAN_MAP.get(roman.upper())


def _normalize_toc_lines(lines: list[str], limit: int = 80) -> list[str]:
    normalized: list[str] = []
    for i, line in enumerate(lines):
        if i < limit:
            line = ROMAN_DOT_GLUE_RE.sub(r"\1. \2", line)
            line = TOC_INLINE_SPLIT_RE.sub(r"\1\n\2. ", line)
        normalized.extend(line.splitlines())
    return normalized


def _extract_toc(lines: list[str]) -> tuple[dict[int, str], int]:
    toc: dict[int, str] = {}
    toc_end = 0
    seen = 0
    for idx, line in enumerate(lines):
        s = line.strip()
        if not s:
            if seen >= 3:
                toc_end = idx + 1
                break
            continue
        m = ROMAN_RE.match(s)
        if m:
            num = _roman_to_int(m.group(1))
            if num is not None:
                toc[num] = m.group(2).strip()
                seen += 1
                toc_end = idx + 1
                continue
        if ROMAN_ONLY_RE.match(s) and seen >= 1:
            toc_end = idx + 1
            continue
        if seen >= 3:
            toc_end = idx
            break
    return toc, toc_end


def _insert_heading(num: int, toc: dict[int, str]) -> str:
    title = toc.get(num, f"Chapter {num:02d}")
    return f"{num}. {title}"


def _preprocess_text(text: str) -> str:
    raw_lines = text.splitlines()

    toc: dict[int, str] = {}
    toc_scan = raw_lines[:40]
    toc_item_re = re.compile(r"\b([IVXLCDM]+)\.\s*([^\d\n]+?)(?=(?:\s+[IVXLCDM]+\.|$))")
    for line in toc_scan:
        fixed = ROMAN_DOT_GLUE_RE.sub(r"\1. \2", line)
        for m in toc_item_re.finditer(fixed):
            num = _roman_to_int(m.group(1))
            if num is not None:
                toc[num] = m.group(2).strip().rstrip(".")

    start_idx = 0
    for i, line in enumerate(raw_lines):
        s = line.strip()
        if not s:
            continue
        if ROMAN_ONLY_RE.match(s) or ROMAN_RE.match(s):
            continue
        if len(s) > 80 and any(ch.islower() for ch in s):
            start_idx = i
            break

    content_lines = raw_lines[start_idx:]
    out_lines: list[str] = []
    last_heading_num: int | None = None

    if toc:
        out_lines.append(_insert_heading(1, toc))
        out_lines.append("")
        last_heading_num = 1

    for line in content_lines:
        s = line.strip()
        if not s:
            out_lines.append(line)
            continue

        if ROMAN_ONLY_RE.match(s):
            num = _roman_to_int(s.rstrip("."))
            if num is None:
                continue
            if last_heading_num == num:
                continue
            out_lines.append(_insert_heading(num, toc))
            out_lines.append("")
            last_heading_num = num
            continue

        m = ROMAN_RE.match(s)
        if m:
            num = _roman_to_int(m.group(1))
            if num is not None:
                if last_heading_num == num:
                    continue
                out_lines.append(_insert_heading(num, toc))
                out_lines.append("")
                last_heading_num = num
                continue

        out_lines.append(line)

    return "\n".join(out_lines).strip() + "\n"


def split_by_chapters(text: str) -> tuple[str, list[str]]:
    """
    Returns:
      front (str), chapters (list[str])
    Each chapter is strictly bounded: from its heading to the next heading.
    """
    text = _preprocess_text(text)
    lines = text.splitlines()
    heading_idx: list[int] = []
    for i, line in enumerate(lines):
        if _is_heading(line):
            heading_idx.append(i)

    if not heading_idx:
        raise RuntimeError("No chapter headings detected. Update heading rules.")

    front = "\n".join(lines[:heading_idx[0]]).strip()
    chapters: list[str] = []

    for i, start in enumerate(heading_idx):
        end = heading_idx[i + 1] if i + 1 < len(heading_idx) else len(lines)
        chunk = "\n".join(lines[start:end]).strip()
        chapters.append(chunk)

    return front, chapters


def validate_chapters(chapters: list[str]) -> None:
    """
    Enforces rule: no spillover. Every file starts with a chapter heading.
    """
    for idx, ch in enumerate(chapters, start=1):
        first_line = ch.splitlines()[0].strip()
        if not _is_heading(first_line):
            raise RuntimeError(
                f"Chapter {idx:02d} does not start with a valid heading: {first_line!r}"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=1, help="1-based chapter start")
    parser.add_argument("--end", type=int, default=0, help="1-based chapter end (0 = all)")
    parser.add_argument("--max", type=int, default=0, help="max chapters to process in this run")
    args = parser.parse_args()

    base_dir = Path("data/builds/book01_the_adventures_of_sherlock_holmes/en")
    src = base_dir / "merge_refine.txt"

    split_dir = base_dir / "split_chapters"
    refined_dir = base_dir / "refined_chapters"

    if not src.exists():
        raise FileNotFoundError(f"Missing input: {src}")

    text = read_text(src)
    front, chapters = split_by_chapters(text)
    validate_chapters(chapters)

    if front:
        write_text(split_dir / "front_00.txt", front.strip() + "\n")

    for i, ch in enumerate(chapters, start=1):
        write_text(split_dir / f"ch_{i:02d}.txt", ch.strip() + "\n")

    bad_prefixes = (
        "Here is",
        "I made",
        "Changes:",
        "Note:",
        "As requested",
        "Sure,",
        "Certainly,",
        "Below is",
        "I'll",
    )

    start = max(1, args.start)
    end = args.end if args.end and args.end >= start else len(chapters)
    remaining_cap = args.max if args.max and args.max > 0 else None

    processed = 0
    for i in range(start, end + 1):
        in_path = split_dir / f"ch_{i:02d}.txt"
        out_path = refined_dir / f"ch_{i:02d}.txt"

        if out_path.exists() and out_path.stat().st_size > 0:
            continue

        raw = read_text(in_path)
        refined = call_agent_text(agent_name="Aldebaran", text=raw)

        if refined.lstrip().startswith(bad_prefixes):
            refined = call_agent_text(
                agent_name="Aldebaran",
                text="OUTPUT ONLY THE REFINED TEXT. NO NOTES. NO HEADINGS. NO COMMENTS.\n\n"
                + raw,
            )

        write_text(out_path, refined.strip() + "\n")
        processed += 1
        if remaining_cap is not None and processed >= remaining_cap:
            break

    print("OK: Split + Refine (Aldebaran) completed. STOPPING HERE (no Polish, no merge).")
    print(f"- Split chapters:   {split_dir}")
    print(f"- Refined chapters: {refined_dir}")


if __name__ == "__main__":
    main()
