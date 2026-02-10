#!/usr/bin/env python3
from pathlib import Path
import json
from gaiden.translate_engine_v1 import translate_book_chunks, merge_translated_chunks

def load_contract(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", required=True)
    args = ap.parse_args()

    c = load_contract(args.contract)
    assert c["schema"] == "gaiden_translate_multilang_v1"
    assert c["mode"] == "one_book_to_many_languages"

    book = c["book"]
    source_lang = c["source_lang"]
    targets = c["target_languages"]

    paths = c["paths"]
    chunks_root = Path(paths["chunks_root"])
    translated_root = Path(paths["translated_root"])

    run = c["run"]
    dry_run = bool(run.get("dry_run", True))
    resume = bool(run.get("resume", True))
    fail_fast = bool(run.get("fail_fast", True))

    for lang in targets:
        try:
            translate_book_chunks(
                book=book,
                source_lang=source_lang,
                target_lang=lang,
                chunks_root=chunks_root,
                translated_root=translated_root,
                resume=resume,
                dry_run=dry_run,
            )
            out_path = translated_root / book / lang / f"{book}_{lang}_merged_v1.txt"
            merge_translated_chunks(
                book=book,
                target_lang=lang,
                translated_root=translated_root,
                out_path=out_path,
            )
            print(f"[OK] {book} -> {lang} (dry_run={dry_run}) merged={out_path}")
        except Exception as e:
            print(f"[FAIL] {book} -> {lang}: {e}")
            if fail_fast:
                raise

if __name__ == "__main__":
    main()
