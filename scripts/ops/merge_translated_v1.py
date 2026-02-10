#!/usr/bin/env python3
from pathlib import Path
from gaiden.translate_engine_v1 import merge_translated_chunks

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True)
    ap.add_argument("--lang", required=True)
    args = ap.parse_args()

    translated_root = Path("data/translated")
    out_path = translated_root / args.book / args.lang / f"{args.book}_{args.lang}_merged_v1.txt"

    stamp = merge_translated_chunks(
        book=args.book,
        target_lang=args.lang,
        translated_root=translated_root,
        out_path=out_path,
    )
    print(f"[OK] merged: {out_path}")
    print(f"[OK] stamp: {out_path}.STAMP.json")

if __name__ == "__main__":
    main()
