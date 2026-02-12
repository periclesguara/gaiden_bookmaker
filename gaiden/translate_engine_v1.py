from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from gaiden.lang import normalize_lang_code, normalize_source_lang
from gaiden.openai_client import get_client, choose_model

# IMPORTANT:
# This engine is intentionally minimal and "add-only".
# It does not refactor existing gaiden.translate.py.
# It provides a stable CLI bridge for Matrix Gaiden.

DEFAULT_FILE_GLOB = "ch_*_chunk_*.txt"

UNIVERSAL_SYSTEM_PROMPT = """You are a senior literary translator.

HARD CONSTRAINTS:
- Do NOT summarize, cut, expand, or reorder content.
- Do NOT change paragraph structure.
- Do NOT add titles, indexes, footnotes, notes, or frontmatter.
- Do NOT use Markdown.
- Do NOT add comments or explanations.
- Do NOT alter proper names, places, dates, or chapter numbering semantics.

QUALITY RULES (Modern 2026):
- Produce a modern, natural, fluent translation.
- Reduce machine-translation redundancy (accidental repeated words).
- Reduce archaic phrasing when it harms readability, without changing meaning.
- If a sentence is excessively long, you may split it into shorter sentences ONLY to improve clarity and ONLY without meaning loss.
- Keep tone and narrative intent.

Return ONLY the translated text.
"""

LANG_TARGET_LABELS = {
    # "en_modern" is still English output; treat as controlled modernization.
    "en_modern": "Modern English (2026)",
    "en_2026": "Modern English (2026)",
    "de": "Modern German (2026)",
    "fr": "Modern French (2026)",
    "es": "Modern Spanish (Latin American neutral, 2026)",
    "ptbr": "Modern Brazilian Portuguese (neutral, 2026)",
    "it": "Modern Italian (2026)",
}

def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"

def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def _write_text(p: Path, s: str) -> None:
    p.write_text(s, encoding="utf-8")

def _sorted_chunk_files(chunks_dir: Path, file_glob: str) -> List[Path]:
    files = sorted([p for p in chunks_dir.glob(file_glob) if p.is_file()])
    return files

def _load_json(p: Path) -> Dict:
    return json.loads(_read_text(p))

def _dump_json(p: Path, data: Dict) -> None:
    _write_text(p, json.dumps(data, ensure_ascii=False, indent=2))

def _make_lang_system_prompt(target_lang: str) -> str:
    label = LANG_TARGET_LABELS.get(target_lang, target_lang)
    return f"{UNIVERSAL_SYSTEM_PROMPT}\n\nTARGET LANGUAGE: {label}\n"


def _load_contract(path: Path) -> Dict:
    return json.loads(_read_text(path))


def _render_user_prompt(template: str, text: str) -> str:
    if "{{TEXT}}" in template:
        return template.replace("{{TEXT}}", text)
    if "{text}" in template:
        return template.replace("{text}", text)
    return template.replace("{TEXT}", text)


def _assert_translate_contract(contract: Dict) -> str:
    stage = str(contract.get("stage", "")).strip()
    model = str(contract.get("model", "")).strip()
    model_lock = contract.get("model_lock", None)
    if stage != "translate":
        raise RuntimeError(f"TRANSLATE MODEL VIOLATION: stage=translate requires model=gpt-5.2 (contract says stage={stage})")
    if model_lock is not True:
        raise RuntimeError(
            "TRANSLATE MODEL VIOLATION: stage=translate requires model_lock=true"
        )
    if model != "gpt-5.2":
        raise RuntimeError(
            f"TRANSLATE MODEL VIOLATION: stage=translate requires model=gpt-5.2 (contract says {model})"
        )
    return model

def call_openai_gpt52_translate(text: str, system_prompt: str, *, model: str) -> str:
    client = get_client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        temperature=0.2,
    )

    return response.choices[0].message.content

def translate_book_chunks(
    book: str,
    source_lang: str,
    target_lang: str,
    chunks_root: Path,
    translated_root: Path,
    file_glob: str = DEFAULT_FILE_GLOB,
    resume: bool = True,
    dry_run: bool = True,
    contract_path: Path | str | None = None,
    contract: Dict | None = None,
    runs_root: Path | None = None,
    run_id: str | None = None,
) -> Dict:
    source_lang = normalize_source_lang(source_lang, default="en")
    target_lang = normalize_lang_code(target_lang, default="en_modern")
    in_dir = chunks_root / book / source_lang
    out_dir = translated_root / book / target_lang
    _ensure_dir(out_dir)

    if contract is None:
        if not contract_path:
            raise RuntimeError("Translate contract is required (contract_path not provided).")
        contract = _load_contract(Path(contract_path))

    resolved_model = _assert_translate_contract(contract)
    model_effective = choose_model(stage="translate", contract_model=resolved_model, env_default=None)
    system_prompt = str(contract.get("system_prompt", "")).strip()
    user_template = str(contract.get("user_prompt", "")).strip()
    if not system_prompt or not user_template:
        raise RuntimeError("Translate contract must include system_prompt and user_prompt.")

    files = _sorted_chunk_files(in_dir, file_glob)
    if not files:
        raise RuntimeError(f"No chunk files found: {in_dir}/{file_glob}")

    report = {
        "schema": "gaiden_translate_run_report_v1",
        "book": book,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "model": model_effective,
        "contract_path": str(contract_path) if contract_path else None,
        "contract_name": contract.get("name"),
        "started_at": _utc_now(),
        "input_dir": str(in_dir),
        "output_dir": str(out_dir),
        "file_glob": file_glob,
        "items": [],
    }

    for fp in files:
        out_fp = out_dir / fp.name

        if resume and out_fp.exists():
            report["items"].append({"chunk_file": fp.name, "status": "skipped_exists", "output_path": str(out_fp)})
            continue

        src = _read_text(fp)

        if dry_run:
            out = f"[DRY_RUN] {fp.name}\n" + src
            status = "dry_run"
        else:
            user_text = _render_user_prompt(user_template, src)
            out = call_openai_gpt52_translate(user_text, system_prompt=system_prompt, model=model_effective)
            status = "translated"

        _write_text(out_fp, out)
        if runs_root and run_id:
            run_dir = runs_root / run_id
            outputs_dir = run_dir / "outputs" / book / target_lang
            _ensure_dir(outputs_dir)
            try:
                shutil.copy2(out_fp, outputs_dir / out_fp.name)
            except Exception:
                pass
        report["items"].append({"chunk_file": fp.name, "status": status, "output_path": str(out_fp)})

    report["finished_at"] = _utc_now()
    _dump_json(out_dir / "translate_run_report.json", report)
    if runs_root and run_id:
        run_dir = runs_root / run_id
        _ensure_dir(run_dir)
        report_path = run_dir / f"translate_run_report_{book}_{target_lang}.json"
        _dump_json(report_path, report)
        default_report = run_dir / "translate_run_report.json"
        if not default_report.exists():
            _dump_json(default_report, report)
    return report

def merge_translated_chunks(
    book: str,
    target_lang: str,
    translated_root: Path,
    out_path: Path,
    file_glob: str = DEFAULT_FILE_GLOB,
) -> Dict:
    target_lang = normalize_lang_code(target_lang, default="en_modern")
    in_dir = translated_root / book / target_lang
    files = _sorted_chunk_files(in_dir, file_glob)
    if not files:
        raise RuntimeError(f"No translated chunks found: {in_dir}/{file_glob}")

    parts = []
    for fp in files:
        parts.append(_read_text(fp).rstrip() + "\n")

    merged = "\n".join(parts).rstrip() + "\n"
    _ensure_dir(out_path.parent)
    _write_text(out_path, merged)

    stamp = {
        "schema": "gaiden_merge_stamp_v1",
        "book": book,
        "target_lang": target_lang,
        "merged_at": _utc_now(),
        "chunk_count": len(files),
        "first": files[0].name,
        "last": files[-1].name,
        "output_path": str(out_path),
    }
    _dump_json(Path(str(out_path) + ".STAMP.json"), stamp)
    return stamp
