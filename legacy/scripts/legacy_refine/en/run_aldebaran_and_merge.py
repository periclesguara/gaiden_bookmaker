#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from pathlib import Path
import sys

# --- CONFIG ---
BOOK_DIR = Path("data/builds/book01_the_adventures_of_sherlock_holmes/en")
SPLIT_DIR = BOOK_DIR / "split_chapters"
RETURN_DIR = BOOK_DIR / "return"
OUT_DIR = RETURN_DIR / "aldebaran_out"
MERGED_FILE = RETURN_DIR / "aldebaran_merge_refine_en.txt"
AGENT_NAME = "Aldebaran"

# Gate: forbid meta-chat
BAD_PREFIXES = (
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

# Optional context banner (helps prevent summarization / “completion” behavior)
BANNER = (
    "[CONTEXT: This is one of the fragments extracted from a larger literary work. "
    "Do NOT summarize, condense, infer missing context, or add notes. "
    "Return ONLY the refined text. The pipeline will merge fragments later.]\n\n"
)


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def list_chapters() -> list[Path]:
    files = sorted(SPLIT_DIR.glob("ch_*.txt"))
    if not files:
        raise RuntimeError(f"No chapter splits found in {SPLIT_DIR}. Expected ch_*.txt")
    return files


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=1, help="1-based chapter start")
    parser.add_argument("--end", type=int, default=0, help="1-based chapter end (0 = all)")
    parser.add_argument("--max", type=int, default=0, help="max chapters to process in this run")
    args = parser.parse_args()

    if not SPLIT_DIR.exists():
        raise FileNotFoundError(f"Missing split dir: {SPLIT_DIR}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RETURN_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from gaiden.openai_client import call_agent_text
    except Exception as exc:
        raise RuntimeError(
            "Failed importing call_agent_text from gaiden.openai_client. "
            "Check openai_client.py and PYTHONPATH."
        ) from exc

    chapters = list_chapters()
    start = max(1, args.start)
    end = args.end if args.end and args.end >= start else len(chapters)
    max_count = args.max if args.max and args.max > 0 else None

    front_path = SPLIT_DIR / "front_00.txt"
    front_text = read_text(front_path).strip() if front_path.exists() else ""

    refined_blocks = []
    failures = 0

    processed = 0
    for idx, ch_path in enumerate(chapters, start=1):
        if idx < start or idx > end:
            continue
        raw = read_text(ch_path).strip()
        payload = BANNER + raw

        out_path = OUT_DIR / ch_path.name
        if out_path.exists() and out_path.stat().st_size > 0:
            refined_blocks.append(out_path.read_text(encoding="utf-8").strip())
            print(f"SKIP {idx:02d}/{len(chapters)} -> {out_path}")
            continue

        refined = call_agent_text(agent_name=AGENT_NAME, text=payload)

        if refined.lstrip().startswith(BAD_PREFIXES):
            refined = call_agent_text(
                agent_name=AGENT_NAME,
                text="OUTPUT ONLY THE REFINED TEXT. NO NOTES. NO HEADINGS. NO COMMENTS.\n\n" + raw,
            )

        refined = refined.strip()

        if not refined:
            failures += 1
            print(f"[WARN] Empty output for {ch_path.name}", file=sys.stderr)

        write_text(out_path, refined + "\n")

        refined_blocks.append(refined)

        print(f"OK {idx:02d}/{len(chapters)} -> {out_path}")

        processed += 1
        if max_count is not None and processed >= max_count:
            break
    merged_parts = []
    if front_text:
        merged_parts.append(front_text)

    merged_parts.extend(refined_blocks)

    merged = "\n\n".join([p.strip() for p in merged_parts if p.strip()]).strip() + "\n"
    write_text(MERGED_FILE, merged)

    print("\nDONE:")
    print(f"- Chapter outputs: {OUT_DIR}  ({len(chapters)} files)")
    print(f"- Merged output:   {MERGED_FILE}")
    if failures:
        print(f"- WARN: {failures} chapter(s) produced empty output", file=sys.stderr)


if __name__ == "__main__":
    main()
