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
    assert c["schema"] == "gaiden_translate_queue_v1"
    assert c["mode"] == "many_books_to_one_language"

    source_lang = c["source_lang"]
    target_lang = c["target_lang"]
    queue = c["queue"]

    paths = c["paths"]
    chunks_root = Path(paths["chunks_root"])
    translated_root = Path(paths["translated_root"])
    runs_root = Path(paths["runs_root"])
    runs_root.mkdir(parents=True, exist_ok=True)

    run = c["run"]
    dry_run = bool(run.get("dry_run", True))
    resume = bool(run.get("resume", True))
    fail_fast = bool(run.get("fail_fast", True))

    summary = {
        "schema": "gaiden_translate_queue_summary_v1",
        "mode": c["mode"],
        "source_lang": source_lang,
        "target_lang": target_lang,
        "items": [],
    }

    for item in queue:
        book = item["book"]
        try:
            translate_book_chunks(
                book=book,
                source_lang=source_lang,
                target_lang=target_lang,
                chunks_root=chunks_root,
                translated_root=translated_root,
                resume=resume,
                dry_run=dry_run,
            )
            out_path = translated_root / book / target_lang / f"{book}_{target_lang}_merged_v1.txt"
            merge_translated_chunks(
                book=book,
                target_lang=target_lang,
                translated_root=translated_root,
                out_path=out_path,
            )
            summary["items"].append({"book": book, "status": "ok", "merged": str(out_path)})
            print(f"[OK] {book} -> {target_lang} (dry_run={dry_run}) merged={out_path}")
        except Exception as e:
            summary["items"].append({"book": book, "status": "fail", "error": str(e)})
            print(f"[FAIL] {book} -> {target_lang}: {e}")
            if fail_fast:
                break

    stamp = runs_root / "translate_queue_last.json"
    stamp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] queue summary written: {stamp}")

if __name__ == "__main__":
    main()
