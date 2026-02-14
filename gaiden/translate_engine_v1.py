from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def _sha256_json(data: Dict) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return _sha256_text(raw)

def _ratio_ok(len_in: int, len_out: int, *, min_ratio: float, max_ratio: float) -> tuple[bool, float | None]:
    if len_in <= 0:
        return True, None
    ratio = len_out / len_in
    return (min_ratio <= ratio <= max_ratio), ratio

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

def call_openai_gpt52_translate(
    text: str,
    system_prompt: str,
    *,
    model: str,
    temperature: float,
    max_output_tokens: int,
) -> tuple[str, Dict | None, str | None]:
    client = get_client()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        temperature=temperature,
        max_tokens=max_output_tokens,
    )

    finish_reason = None
    try:
        finish_reason = response.choices[0].finish_reason
    except Exception:
        finish_reason = None

    usage = None
    try:
        u = getattr(response, "usage", None)
        if u is not None:
            usage = {
                "prompt_tokens": getattr(u, "prompt_tokens", None),
                "completion_tokens": getattr(u, "completion_tokens", None),
                "total_tokens": getattr(u, "total_tokens", None),
            }
    except Exception:
        usage = None

    return response.choices[0].message.content, usage, finish_reason

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
    temperature = float(contract.get("temperature", 0.3))
    max_output_tokens = int(contract.get("max_output_tokens", 2400))
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
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "started_at": _utc_now(),
        "input_dir": str(in_dir),
        "output_dir": str(out_dir),
        "file_glob": file_glob,
        "validation_ratio_min": 0.95,
        "validation_ratio_max": 1.05,
        "items": [],
    }

    for fp in files:
        out_fp = out_dir / fp.name
        meta_fp = out_dir / f"{fp.stem}.meta.json"

        if resume and out_fp.exists():
            report["items"].append(
                {
                    "chunk_file": fp.name,
                    "status": "skipped_exists",
                    "output_path": str(out_fp),
                    "meta_path": str(meta_fp),
                }
            )
            continue

        src = _read_text(fp)
        user_text = _render_user_prompt(user_template, src)
        len_in = len(src)
        chunk_started_at = _utc_now()

        max_attempts = 2
        attempt = 0
        out = ""
        status = "translated"
        usage = None
        finish_reason = None
        ratio = None
        truncated = False

        while True:
            attempt += 1
            if dry_run:
                out = f"[DRY_RUN] {fp.name}\n" + src
                status = "dry_run"
                usage = None
                finish_reason = None
                truncated = False
                ok_ratio = True
                ratio = None
            else:
                out, usage, finish_reason = call_openai_gpt52_translate(
                    user_text,
                    system_prompt=system_prompt,
                    model=model_effective,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                )
                status = "translated"
                len_out = len(out)
                ok_ratio, ratio = _ratio_ok(len_in, len_out, min_ratio=0.95, max_ratio=1.05)
                truncated = (finish_reason == "length")

                if ok_ratio and not truncated:
                    break

                if attempt >= max_attempts:
                    raise RuntimeError(
                        f"Chunk size ratio out of bounds or truncated: {fp.name} "
                        f"(ratio={ratio}, finish_reason={finish_reason})"
                    )
                continue

            break

        _write_text(out_fp, out)
        chunk_finished_at = _utc_now()

        meta = {
            "schema": "gaiden_translate_chunk_meta_v1",
            "book": book,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "chunk_file": fp.name,
            "input_chunk_path": str(fp),
            "output_chunk_path": str(out_fp),
            "contract_path": str(contract_path) if contract_path else None,
            "contract_sha256": _sha256_json(contract),
            "system_prompt_sha256": _sha256_text(system_prompt),
            "user_prompt_sha256": _sha256_text(user_template),
            "rendered_prompt_sha256": _sha256_text(user_text),
            "model": model_effective,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "len_in_chars": len_in,
            "len_out_chars": len(out),
            "ratio": ratio,
            "finish_reason": finish_reason,
            "truncated": bool(truncated),
            "attempts": attempt,
            "status": status,
            "started_at": chunk_started_at,
            "finished_at": chunk_finished_at,
        }
        _dump_json(meta_fp, meta)

        if runs_root and run_id:
            run_dir = runs_root / run_id
            outputs_dir = run_dir / "outputs" / book / target_lang
            _ensure_dir(outputs_dir)
            try:
                shutil.copy2(out_fp, outputs_dir / out_fp.name)
            except Exception:
                pass
        report["items"].append(
            {
                "chunk_file": fp.name,
                "status": status,
                "output_path": str(out_fp),
                "meta_path": str(meta_fp),
                "len_in_chars": len_in,
                "len_out_chars": len(out),
                "ratio": ratio,
                "finish_reason": finish_reason,
                "truncated": bool(truncated),
                "attempts": attempt,
            }
        )

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


def _report_failure_reason(report: Dict) -> str | None:
    status = str(report.get("status", "")).strip()
    if status == "error_preflight":
        return "error_preflight"

    items = report.get("items") or []
    min_ratio = float(report.get("validation_ratio_min", 0.95))
    max_ratio = float(report.get("validation_ratio_max", 1.05))
    for item in items:
        item_status = str(item.get("status", "")).strip()
        if item_status and item_status not in {"translated", "skipped_exists", "dry_run"}:
            return f"chunk_status={item_status}"

        finish_reason = str(item.get("finish_reason") or "").strip().lower()
        if finish_reason in {"content_filter", "incomplete", "length"}:
            return f"finish_reason={finish_reason}"

        if item.get("truncated") is True:
            return "truncated"

        if item.get("structure_ok") is False:
            return "structure_ok_false"

        ratio = item.get("ratio")
        if isinstance(ratio, (int, float)):
            if ratio < 0.85:
                return f"ratio_guard={ratio:.3f}"
            if ratio < min_ratio or ratio > max_ratio:
                return f"ratio_out_of_bounds={ratio:.3f}"
    return None


def _load_json_safe(path: Path) -> Dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def run_translate_safe(
    *,
    book: str,
    source_lang: str,
    target_lang: str,
    chunks_root: Path,
    translated_root: Path,
    resume: bool = True,
    dry_run: bool = True,
    contract_path: Path | str | None = None,
    contract: Dict | None = None,
    runs_root: Path | None = None,
    run_id: str | None = None,
    out_path: Path | None = None,
    fallback_temperature: float = 0.0,
    fallback_max_output_tokens: int = 8000,
) -> Dict:
    """
    Translate with official GPT-5.2 flow; on failure, fall back to ALAMAGUEDERAZ agent.
    Returns a dict with status: ok_official | ok_fallback | error.
    """
    book = str(book)
    source_lang = normalize_source_lang(source_lang, default="en")
    target_lang = normalize_lang_code(target_lang, default="en_modern")
    in_dir = chunks_root / book / source_lang
    out_dir = translated_root / book / target_lang
    official_report_path = out_dir / "translate_run_report.json"

    result: Dict[str, Any] = {
        "status": "error",
        "official_report": None,
        "official_error": None,
        "fallback_report": None,
        "merged_txt": None,
        "merged_len": None,
        "merged_count": None,
    }

    try:
        report = translate_book_chunks(
            book=book,
            source_lang=source_lang,
            target_lang=target_lang,
            chunks_root=chunks_root,
            translated_root=translated_root,
            resume=resume,
            dry_run=dry_run,
            contract_path=contract_path,
            contract=contract,
            runs_root=runs_root,
            run_id=run_id,
        )
        failure = None if dry_run else _report_failure_reason(report)
        if failure:
            raise RuntimeError(f"OFFICIAL_VALIDATE_FAIL: {failure}")
        if out_path:
            merge_translated_chunks(
                book=book,
                target_lang=target_lang,
                translated_root=translated_root,
                out_path=out_path,
            )
        if official_report_path.exists():
            result["official_report"] = str(official_report_path)
        print("[TRANSLATE] official OK")
        result["status"] = "ok_official"
        return result
    except Exception as exc:
        result["official_error"] = repr(exc)
        if official_report_path.exists():
            result["official_report"] = str(official_report_path)

    if dry_run:
        return result

    print("[TRANSLATE] official FAILED -> fallback ALAMAGUEDERAZ")

    repo_root = Path(__file__).resolve().parents[1]
    cmd = [
        sys.executable,
        str(repo_root / "gaiden" / "tools" / "agent_translate_default.py"),
        "--book-id",
        book,
        "--chunk-dir",
        str(in_dir),
        "--out-dir",
        str(out_dir),
        "--agent",
        "ALAMAGUEDERAZ",
        "--suffix",
        target_lang,
        "--temperature",
        str(fallback_temperature),
        "--max-output-tokens",
        str(fallback_max_output_tokens),
    ]
    env = os.environ.copy()
    py_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{py_path}" if py_path else str(repo_root)
    proc = subprocess.run(
        cmd,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    fallback_report_path = out_dir / "agent_translate_run_report.json"
    fallback_report = _load_json_safe(fallback_report_path)
    if fallback_report_path.exists():
        result["fallback_report"] = str(fallback_report_path)

    if proc.returncode == 0 and fallback_report and fallback_report.get("status") == "ok":
        result["status"] = "ok_fallback"
        result["merged_txt"] = fallback_report.get("merged_txt")
        result["merged_len"] = fallback_report.get("merged_len")
        result["merged_count"] = fallback_report.get("merged_count")
        print(f"[TRANSLATE] fallback OK merged={result['merged_txt']}")
        return result

    result["fallback_error"] = {
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }
    return result
