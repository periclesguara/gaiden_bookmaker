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
from gaiden.secrets_loader import require_openai_ready

def load_contract(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract")
    ap.add_argument("--book")
    ap.add_argument("--src", default="en")
    ap.add_argument("--targets", help="comma-separated target langs")
    ap.add_argument("--dry-run", default=None)
    ap.add_argument("--resume", default=None)
    ap.add_argument("--fail-fast", default=None)
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
        assert c["schema"] == "gaiden_translate_multilang_v1"
        assert c["mode"] == "one_book_to_many_languages"
        book = c["book"]
        source_lang = normalize_source_lang(c["source_lang"], default="en")
        targets = [normalize_lang_code(t, default="en_modern") for t in c["target_languages"]]
        paths = c["paths"]
        chunks_root = Path(paths["chunks_root"])
        translated_root = Path(paths["translated_root"])
        runs_root = Path(paths["runs_root"])
        run = c["run"]
        dry_run = bool(run.get("dry_run", True))
        resume = bool(run.get("resume", True))
        fail_fast = bool(run.get("fail_fast", True))
    else:
        if not args.book or not args.targets:
            raise SystemExit("--book and --targets are required when --contract is not provided.")
        book = args.book.strip()
        source_lang = normalize_source_lang(args.src, default="en")
        targets = [normalize_lang_code(t.strip(), default="en_modern") for t in args.targets.split(",") if t.strip()]
        chunks_root = Path("data/chunks")
        translated_root = Path("data/translated")
        runs_root = translated_root / "_runs"
        dry_run = _parse_bool(args.dry_run, True)
        resume = _parse_bool(args.resume, True)
        fail_fast = _parse_bool(args.fail_fast, True)

    warnings = []

    if dry_run:
        warnings.append("DRY RUN ativo: nenhuma chamada à OpenAI será feita.")
    for w in warnings:
        print(f"[WARN] {w}")

    try:
        require_openai_ready(dry_run=dry_run)
    except Exception as exc:
        print(f"[WARN] preflight failed (will attempt fallback if needed): {exc}")

    # Persist contract into runtime directory for traceability.
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    run_id = f"multilang_{ts}"
    runs_root.mkdir(parents=True, exist_ok=True)
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
        extra={"mode": "multilang", "book": book, "targets": targets},
    )
    contracts_dir = run_dir / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "schema": "gaiden_translate_multilang_summary_v1",
        "mode": "multilang",
        "book": book,
        "source_lang": source_lang,
        "targets": targets,
        "run_id": run_id,
        "items": [],
    }

    for idx, lang in enumerate(targets):
        try:
            contract_path = resolve_translate_contract_path(lang)
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            if idx == 0:
                write_contract_json(run_dir, contract)
            contracts_dir.joinpath(f"translate_{lang}.json").write_text(
                json.dumps(contract, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            contracts_root.joinpath(f"translate_multilang_{ts}_{lang}.json").write_text(
                json.dumps(contract, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            out_path = translated_root / book / lang / f"{book}_{lang}_merged_v1.txt"
            result = run_translate_safe(
                book=book,
                source_lang=source_lang,
                target_lang=lang,
                chunks_root=chunks_root,
                translated_root=translated_root,
                resume=resume,
                dry_run=dry_run,
                contract_path=contract_path,
                runs_root=runs_root,
                run_id=run_id,
                out_path=out_path,
            )

            merged_path = result.get("merged_txt") or str(out_path)
            if dry_run and result["status"] == "dry_run":
                print(f"[DRY] {book} -> {lang} (dry_run)")
                summary["items"].append(
                    {
                        "lang": lang,
                        "status": "dry_run",
                        "official_report": result.get("official_report"),
                        "fallback_report": result.get("fallback_report"),
                    }
                )
                continue
            if result["status"] == "ok_official":
                print(f"[OK] {book} -> {lang} (dry_run={dry_run}) merged={out_path}")
                merged_copy = run_dir / f"merged_v1_{lang}.txt"
                shutil.copy2(out_path, merged_copy)
                if idx == 0:
                    shutil.copy2(out_path, run_dir / "merged_v1.txt")
            elif result["status"] == "ok_fallback":
                print(f"[OK] {book} -> {lang} (fallback) merged={merged_path}")
                if merged_path:
                    merged_copy = run_dir / f"merged_fallback_{lang}.txt"
                    shutil.copy2(merged_path, merged_copy)
                    if idx == 0:
                        shutil.copy2(merged_path, run_dir / "merged_fallback.txt")
            else:
                err = result.get("official_error") or "translate_failed"
                print(f"[FAIL] {book} -> {lang}: {err}")
                summary["items"].append(
                    {
                        "lang": lang,
                        "status": "error",
                        "error": err,
                        "official_report": result.get("official_report"),
                        "fallback_report": result.get("fallback_report"),
                    }
                )
                if fail_fast:
                    raise RuntimeError(err)
                continue

            summary["items"].append(
                {
                    "lang": lang,
                    "status": result["status"],
                    "merged": merged_path,
                    "official_report": result.get("official_report"),
                    "fallback_report": result.get("fallback_report"),
                }
            )
        except Exception as e:
            print(f"[FAIL] {book} -> {lang}: {e}")
            summary["items"].append({"lang": lang, "status": "error", "error": str(e)})
            if fail_fast:
                raise

    summary_path = run_dir / "translate_multilang_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
