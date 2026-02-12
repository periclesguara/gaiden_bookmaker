#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gaiden.env_guard import assert_venv

assert_venv(ROOT)

import argparse
import json
import os
from datetime import datetime
import shutil

from gaiden.contracts_v2.resolver import resolve_translate_contract_path
from gaiden.lang import normalize_lang_code, normalize_source_lang
from gaiden.run_artifacts import write_contract_json, write_env_json
from gaiden.secrets_loader import require_openai_ready
from gaiden.translate_engine_v1 import translate_book_chunks, merge_translated_chunks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True)
    ap.add_argument("--src", default="en")
    ap.add_argument("--tgt", default="en_modern")
    ap.add_argument("--chunk", required=True, help="Nome do arquivo chunk, ex: ch_001_chunk_001.txt")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dry_run = bool(args.dry_run)
    require_openai_ready(dry_run=dry_run)

    book = args.book.strip()
    src = normalize_source_lang(args.src, default="en")
    tgt = normalize_lang_code(args.tgt, default="en_modern")

    chunks_root = Path("data/chunks")
    translated_root = Path("data/translated")
    runs_root = translated_root / "_runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    chunk_path = chunks_root / book / src / args.chunk
    if not chunk_path.is_file():
        raise SystemExit(f"Chunk não encontrado: {chunk_path}")

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    run_id = f"one_chunk_{ts}"
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    contract_path = resolve_translate_contract_path(tgt)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    print("MODEL:", contract.get("model"))
    print("BASE_URL:", os.getenv("OPENAI_BASE_URL"))
    print("INPUT_PATH:", chunk_path)

    write_contract_json(run_dir, contract)
    contracts_root = Path("data") / "contracts_runtime"
    contracts_root.mkdir(parents=True, exist_ok=True)
    runtime_contract_path = contracts_root / f"translate_one_chunk_{ts}.json"
    runtime_contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")

    write_env_json(
        run_dir,
        dry_run=dry_run,
        model=contract.get("model"),
        base_url=os.environ.get("OPENAI_BASE_URL"),
        repo_root=Path(__file__).resolve().parents[2],
    )

    report = translate_book_chunks(
        book=book,
        source_lang=src,
        target_lang=tgt,
        chunks_root=chunks_root,
        translated_root=translated_root,
        file_glob=args.chunk,
        resume=False,
        dry_run=dry_run,
        contract_path=contract_path,
        runs_root=runs_root,
        run_id=run_id,
    )

    out_path = translated_root / book / tgt / f"{book}_{tgt}_merged_v1.txt"
    merge_translated_chunks(
        book=book,
        target_lang=tgt,
        translated_root=translated_root,
        out_path=out_path,
        file_glob=args.chunk,
    )

    tmp_out_dir = Path("/tmp/gaiden_one_chunk") / book / tgt
    tmp_out_dir.mkdir(parents=True, exist_ok=True)
    tmp_out = tmp_out_dir / out_path.name
    shutil.copy2(out_path, tmp_out)
    run_merged = run_dir / "merged_v1.txt"
    shutil.copy2(out_path, run_merged)

    meta = {
        "schema": "gaiden_translate_one_chunk_v1",
        "run_id": run_id,
        "book": book,
        "source_lang": src,
        "target_lang": tgt,
        "model": contract.get("model"),
        "base_url": os.getenv("OPENAI_BASE_URL"),
        "input_path": str(chunk_path),
        "output_path": str(out_path),
        "tmp_output_path": str(tmp_out),
        "dry_run": dry_run,
        "report_items": len(report.get("items", [])),
    }
    meta_path = run_dir / "translate_one_chunk_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print("OUTPUT_PATH:", out_path)
    print("TMP_OUTPUT_PATH:", tmp_out)
    print("RUN_META:", meta_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
