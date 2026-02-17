#!/usr/bin/env python3
import os
import sys
import argparse
from pathlib import Path

from gaiden.tools.agent_translate_default import run_agent_translate


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def validate_chunks(chunk_dir):
    if not os.path.isdir(chunk_dir):
        print(f"[TRANSLATE_DEFAULT] ERROR chunk_dir not found: {chunk_dir}")
        sys.exit(2)

    chunks = sorted(Path(chunk_dir).glob("ch_*_chunk_*.txt"))
    if not chunks:
        print(f"[TRANSLATE_DEFAULT] ERROR no chunks found in {chunk_dir}")
        sys.exit(2)

    return chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("book_id")
    parser.add_argument("suffix")
    parser.add_argument("--chunk-lang", default="en")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-output-tokens", type=int, default=8000)

    args = parser.parse_args()

    book_id = args.book_id
    suffix = args.suffix
    chunk_lang = args.chunk_lang

    chunk_dir = f"data/chunks/{book_id}/{chunk_lang}"
    out_dir = f"data/translated/{book_id}/{suffix}"

    ensure_dir(out_dir)
    validate_chunks(chunk_dir)

    print(f"[TRANSLATE_DEFAULT] START book={book_id} suffix={suffix}")
    print("[TRANSLATE_DEFAULT] agent=ALAMAGUEDERAZ")

    try:
        result = run_agent_translate(
            book_id=book_id,
            chunk_dir=chunk_dir,
            out_dir=out_dir,
            suffix=suffix,
            mode="default",
            temperature=args.temperature,
            max_output_tokens=args.max_output_tokens,
            limit=args.limit,
        )
    except Exception as e:
        print(f"[TRANSLATE_DEFAULT] ERROR {repr(e)}")
        sys.exit(2)

    merged_path = str(result.get("merged_txt") or "").strip()

    if not merged_path or not os.path.exists(merged_path):
        code = int(result.get("exit_code") or 3)
        print(f"[TRANSLATE_DEFAULT] ERROR canonical artifact not generated (exit_code={code})")
        sys.exit(code)

    size = os.path.getsize(merged_path)

    print(f"[TRANSLATE_DEFAULT] DONE merged={merged_path} bytes={size}")
    sys.exit(0)


if __name__ == "__main__":
    main()
