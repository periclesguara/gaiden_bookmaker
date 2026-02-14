#!/usr/bin/env python3
import os
import sys
import json
import argparse
from pathlib import Path

from gaiden.translate_engine_v1 import run_translate_safe


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def validate_chunks(chunk_dir: str):
    if not os.path.isdir(chunk_dir):
        print(f"[TRANSLATE_SAFE] ERROR chunk_dir not found: {chunk_dir}")
        sys.exit(2)

    chunks = sorted(Path(chunk_dir).glob("ch_*.txt"))
    if not chunks:
        print(f"[TRANSLATE_SAFE] ERROR no chunks found in {chunk_dir}")
        sys.exit(2)

    return chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("book_id")
    parser.add_argument("suffix")
    parser.add_argument("--chunk-lang", default="en")
    parser.add_argument("--contract", default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    book_id = args.book_id
    suffix = args.suffix
    chunk_lang = args.chunk_lang

    chunk_dir = f"data/chunks/{book_id}/{chunk_lang}"
    out_dir = f"data/translated/{book_id}/{suffix}"
    contract = args.contract or f"gaiden/contracts_v2/translate/lang/{suffix}_2026.json"

    ensure_dir(out_dir)
    validate_chunks(chunk_dir)

    print(f"[TRANSLATE_SAFE] START book={book_id} suffix={suffix}")

    try:
        result = run_translate_safe(
            book_id=book_id,
            chunk_dir=chunk_dir,
            out_dir=out_dir,
            suffix=suffix,
            contract_path=contract,
            limit=args.limit,
            dry_run=args.dry_run,
        )

    except Exception as e:
        print(f"[TRANSLATE_SAFE] ERROR {repr(e)}")
        sys.exit(2)

    if not result:
        print("[TRANSLATE_SAFE] FAILED")
        sys.exit(2)

    merged_path = os.path.join(out_dir, "merge_refine_clean.txt")

    if not os.path.exists(merged_path):
        print("[TRANSLATE_SAFE] ERROR merge not generated")
        sys.exit(2)

    size = os.path.getsize(merged_path)

    print(f"[TRANSLATE_SAFE] DONE merged={merged_path} bytes={size}")
    sys.exit(0)


if __name__ == "__main__":
    main()
