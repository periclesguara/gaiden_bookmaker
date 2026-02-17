#!/usr/bin/env python3
import os
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

from gaiden.translate_engine_v1 import run_translate_safe
from gaiden.openai_client import openai_healthcheck


def ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def validate_chunks(chunk_dir: str):
    if not os.path.isdir(chunk_dir):
        print(f"[TRANSLATE_SAFE] ERROR chunk_dir not found: {chunk_dir}")
        sys.exit(2)

    chunks = sorted(Path(chunk_dir).glob("ch_*_chunk_*.txt"))
    if not chunks:
        print(f"[TRANSLATE_SAFE] ERROR no chunks found in {chunk_dir}")
        sys.exit(2)

    return chunks


def _write_safe_report(out_dir: str, payload: dict) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "translate_safe_run_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


def preflight_or_abort(book_id: str, out_dir: str, dry_run: bool) -> None:
    if dry_run:
        payload = {
            "schema": "gaiden_translate_safe_v2",
            "book_id": book_id,
            "selected_mode": "automatic",
            "final_mode": "automatic",
            "effective_route": "automatic",
            "status": "dry_run",
            "error": None,
            "skipped_reason": "dry_run",
            "exit_code": 0,
            "ts": datetime.utcnow().isoformat() + "Z",
        }
        _write_safe_report(out_dir, payload)
        print("[TRANSLATE_SAFE] DRY_RUN (no API calls)")
        return

    ok, msg = openai_healthcheck()
    if not ok:
        payload = {
            "schema": "gaiden_translate_safe_v2",
            "book_id": book_id,
            "selected_mode": "automatic",
            "final_mode": "automatic",
            "effective_route": "automatic",
            "status": "error_preflight",
            "error": msg,
            "exit_code": 2,
            "ts": datetime.utcnow().isoformat() + "Z",
        }
        _write_safe_report(out_dir, payload)
        print("[TRANSLATE_SAFE] PRE-FLIGHT FAILED:", msg)
        sys.exit(2)


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
    preflight_or_abort(book_id, out_dir, args.dry_run)
    if args.dry_run:
        sys.exit(0)
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

    status = str(result.get("status") or "")
    if status not in {"ok_official", "ok_fallback"}:
        code = int(result.get("exit_code") or 3)
        print(f"[TRANSLATE_SAFE] ERROR status={status} exit_code={code}")
        sys.exit(code)

    merged_path = str(result.get("merged_txt") or "").strip()
    if not merged_path or not os.path.exists(merged_path):
        print("[TRANSLATE_SAFE] ERROR canonical artifact not generated")
        sys.exit(3)

    size = os.path.getsize(merged_path)
    print(f"[TRANSLATE_SAFE] DONE merged={merged_path} bytes={size}")
    sys.exit(0)


if __name__ == "__main__":
    main()
