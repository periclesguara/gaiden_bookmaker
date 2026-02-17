#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gaiden.env_guard import assert_venv

assert_venv(ROOT)

import json
import os
from datetime import datetime
import shutil

from gaiden.contracts_v2.resolver import resolve_translate_contract_path
from gaiden.lang import normalize_lang_code, normalize_source_lang
from gaiden.run_artifacts import write_contract_json, write_env_json
from gaiden.translate_engine_v1 import run_translate_safe
from gaiden.tools.agent_translate_default import run_agent_translate
from gaiden.secrets_loader import require_openai_ready
from gaiden.translate_artifacts import normalize_mode

def load_contract(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract")
    ap.add_argument("--mode", default="multibook")
    ap.add_argument("--books", help="comma-separated book codes")
    ap.add_argument("--src", default="en")
    ap.add_argument("--tgt", default="en_modern")
    ap.add_argument("--dry-run", default=None)
    ap.add_argument("--resume", default=None)
    ap.add_argument("--fail-fast", default=None)
    ap.add_argument("--translate-mode", default=None)
    args = ap.parse_args()

    def _parse_bool(val, default: bool) -> bool:
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        raw = str(val).strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default

    if args.contract:
        c = load_contract(args.contract)
        assert c["schema"] == "gaiden_translate_queue_v1"
        assert c["mode"] == "many_books_to_one_language"
        run = c.get("run", {})
        dry_run = bool(run.get("dry_run", True))
        resume = bool(run.get("resume", True))
        fail_fast = bool(run.get("fail_fast", True))
        translate_mode = normalize_mode(c.get("translate_mode") or run.get("translate_mode"), default="automatic")
        source_lang = normalize_source_lang(c["source_lang"], default="en")
        target_lang = normalize_lang_code(c["target_lang"], default="en_modern")
        queue = c["queue"]
        paths = c["paths"]
        chunks_root = Path(paths["chunks_root"])
        translated_root = Path(paths["translated_root"])
        runs_root = Path(paths["runs_root"])
    else:
        if args.mode != "multibook":
            raise SystemExit("Only --mode multibook is supported.")
        if not args.books:
            raise SystemExit("--books is required when --contract is not provided.")
        dry_run = _parse_bool(args.dry_run, True)
        resume = _parse_bool(args.resume, True)
        fail_fast = _parse_bool(args.fail_fast, True)
        translate_mode = normalize_mode(args.translate_mode, default="automatic")
        source_lang = normalize_source_lang(args.src, default="en")
        target_lang = normalize_lang_code(args.tgt, default="en_modern")
        queue = [{"book": b.strip()} for b in args.books.split(",") if b.strip()]
        chunks_root = Path("data/chunks")
        translated_root = Path("data/translated")
        runs_root = translated_root / "_runs"

    try:
        require_openai_ready(dry_run=dry_run)
    except Exception as exc:
        print(f"[WARN] preflight failed (will attempt fallback if needed): {exc}")

    runs_root.mkdir(parents=True, exist_ok=True)
    warnings = []

    if dry_run:
        warnings.append("DRY RUN ativo: nenhuma chamada à OpenAI será feita.")
    for w in warnings:
        print(f"[WARN] {w}")

    # Persist contract into runtime directory for traceability.
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    run_id = f"queue_{ts}"
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    contracts_root = Path("data") / "contracts_runtime"
    contracts_root.mkdir(parents=True, exist_ok=True)
    write_env_json(
        run_dir,
        dry_run=dry_run,
        model="gpt-5.2",
        base_url=os.environ.get("OPENAI_BASE_URL"),
        repo_root=Path(__file__).resolve().parents[2],
        extra={"mode": "multibook", "queue": queue, "target_lang": target_lang},
    )
    contracts_dir = run_dir / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "schema": "gaiden_translate_queue_summary_v1",
        "mode": "multibook",
        "source_lang": source_lang,
        "target_lang": target_lang,
        "model": "gpt-5.2",
        "run_id": run_id,
        "translate_mode": translate_mode,
        "items": [],
    }

    contract_path = resolve_translate_contract_path(target_lang)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    write_contract_json(run_dir, contract)
    contracts_dir.joinpath(f"translate_{target_lang}.json").write_text(
        json.dumps(contract, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    contracts_root.joinpath(f"translate_queue_{ts}_{target_lang}.json").write_text(
        json.dumps(contract, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    for item in queue:
        book = item["book"]
        try:
            if translate_mode == "default":
                if dry_run:
                    result = {
                        "status": "dry_run",
                        "selected_mode": "default",
                        "final_mode": "default",
                        "fallback_used": False,
                        "merged_txt": None,
                        "artifact_filename": None,
                        "exit_code": 0,
                    }
                else:
                    run = run_agent_translate(
                        book_id=book,
                        chunk_dir=chunks_root / book / source_lang,
                        out_dir=translated_root / book / target_lang,
                        suffix=target_lang,
                        mode="default",
                    )
                    result = {
                        "status": "ok_default" if run.get("status") == "ok" else "error_default",
                        "selected_mode": "default",
                        "final_mode": "default",
                        "fallback_used": False,
                        "official_report": None,
                        "fallback_report": str((translated_root / book / target_lang / "agent_translate_run_report.json")),
                        "merged_txt": run.get("merged_txt"),
                        "artifact_filename": run.get("artifact_filename"),
                        "exit_code": int(run.get("exit_code") or (0 if run.get("status") == "ok" else 3)),
                    }
            else:
                result = run_translate_safe(
                    book=book,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    chunks_root=chunks_root,
                    translated_root=translated_root,
                    resume=resume,
                    dry_run=dry_run,
                    contract_path=contract_path,
                    runs_root=runs_root,
                    run_id=run_id,
                )
            merged_path = result.get("merged_txt")
            if dry_run and result["status"] == "dry_run":
                summary["items"].append(
                    {
                        "book": book,
                        "status": "dry_run",
                        "selected_mode": translate_mode,
                        "official_report": result.get("official_report"),
                        "fallback_report": result.get("fallback_report"),
                    }
                )
                print(f"[DRY] {book} -> {target_lang} (dry_run)")
                continue
            if result["status"] == "ok_official":
                summary["items"].append(
                    {
                        "book": book,
                        "status": result["status"],
                        "merged": str(merged_path),
                        "selected_mode": result.get("selected_mode", translate_mode),
                        "final_mode": result.get("final_mode", translate_mode),
                        "fallback_used": bool(result.get("fallback_used", False)),
                        "artifact_filename": result.get("artifact_filename"),
                        "exit_code": result.get("exit_code"),
                        "official_report": result.get("official_report"),
                        "fallback_report": result.get("fallback_report"),
                    }
                )
                print(f"[OK] {book} -> {target_lang} (dry_run={dry_run}) merged={merged_path}")
                merged_copy = run_dir / f"merged_v1_{book}.txt"
                shutil.copy2(Path(str(merged_path)), merged_copy)
                if len(summary["items"]) == 1:
                    shutil.copy2(Path(str(merged_path)), run_dir / "merged_v1.txt")
            elif result["status"] == "ok_default":
                summary["items"].append(
                    {
                        "book": book,
                        "status": result["status"],
                        "merged": str(merged_path),
                        "selected_mode": result.get("selected_mode", translate_mode),
                        "final_mode": result.get("final_mode", translate_mode),
                        "fallback_used": bool(result.get("fallback_used", False)),
                        "artifact_filename": result.get("artifact_filename"),
                        "exit_code": result.get("exit_code"),
                        "official_report": result.get("official_report"),
                        "fallback_report": result.get("fallback_report"),
                    }
                )
                print(f"[OK] {book} -> {target_lang} (default) merged={merged_path}")
                if merged_path:
                    merged_copy = run_dir / f"merged_default_{book}.txt"
                    shutil.copy2(Path(str(merged_path)), merged_copy)
                    if len(summary["items"]) == 1:
                        shutil.copy2(Path(str(merged_path)), run_dir / "merged_default.txt")
            elif result["status"] == "ok_fallback":
                summary["items"].append(
                    {
                        "book": book,
                        "status": result["status"],
                        "merged": merged_path,
                        "selected_mode": result.get("selected_mode", translate_mode),
                        "final_mode": result.get("final_mode", translate_mode),
                        "fallback_used": bool(result.get("fallback_used", False)),
                        "artifact_filename": result.get("artifact_filename"),
                        "exit_code": result.get("exit_code"),
                        "official_report": result.get("official_report"),
                        "fallback_report": result.get("fallback_report"),
                    }
                )
                print(f"[OK] {book} -> {target_lang} (fallback) merged={merged_path}")
                if merged_path:
                    merged_copy = run_dir / f"merged_fallback_{book}.txt"
                    shutil.copy2(merged_path, merged_copy)
                    if len(summary["items"]) == 1:
                        shutil.copy2(merged_path, run_dir / "merged_fallback.txt")
            else:
                err = result.get("official_error") or "translate_failed"
                summary["items"].append(
                    {
                        "book": book,
                        "status": "error",
                        "error": err,
                        "official_report": result.get("official_report"),
                        "fallback_report": result.get("fallback_report"),
                    }
                )
                print(f"[FAIL] {book} -> {target_lang}: {err}")
                if fail_fast:
                    raise RuntimeError(err)
                continue
        except Exception as e:
            summary["items"].append({"book": book, "status": "error", "error": str(e)})
            print(f"[FAIL] {book} -> {target_lang}: {e}")
            if fail_fast:
                break

    stamp = runs_root / "translate_queue_last.json"
    stamp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    run_summary = run_dir / "translate_queue_summary.json"
    run_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] queue summary written: {stamp}")
    print(f"[OK] run summary written: {run_summary}")

if __name__ == "__main__":
    main()
