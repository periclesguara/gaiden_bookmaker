from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from gaiden.lang import normalize_lang_code, normalize_source_lang
from gaiden.openai_client import get_client, choose_model
from gaiden.tools.agent_translate_default import resolve_agent_for_target
from gaiden.translate_artifacts import (
    assert_valid_canonical_artifact,
    canonical_meta_path,
    canonical_artifact_path,
    lang_token,
    normalize_book_code,
    normalize_mode,
    sha256_file,
    source_input_hash,
    write_canonical_meta,
    write_active_pointer,
)

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
    allowed_models = {"gpt-5.2", "gpt-5-chat-latest"}
    if stage != "translate":
        raise RuntimeError(
            f"TRANSLATE MODEL VIOLATION: stage=translate requires model in {sorted(allowed_models)} (contract says stage={stage})"
        )
    if model_lock is not True:
        raise RuntimeError(
            "TRANSLATE MODEL VIOLATION: stage=translate requires model_lock=true"
        )
    if model not in allowed_models:
        raise RuntimeError(
            f"TRANSLATE MODEL VIOLATION: stage=translate requires model in {sorted(allowed_models)} (contract says {model})"
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
    request_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
        "temperature": temperature,
    }
    # GPT-5.x rejects max_tokens in chat.completions; use max_completion_tokens.
    if str(model).startswith("gpt-5"):
        request_payload["max_completion_tokens"] = max_output_tokens
    else:
        request_payload["max_tokens"] = max_output_tokens

    response = client.chat.completions.create(**request_payload)

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
    limit: int = 0,
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
    validation_ratio_min = float(contract.get("validation_ratio_min", 0.85))
    validation_ratio_max = float(contract.get("validation_ratio_max", 1.20))
    if not system_prompt or not user_template:
        raise RuntimeError("Translate contract must include system_prompt and user_prompt.")
    if validation_ratio_min <= 0 or validation_ratio_max <= 0 or validation_ratio_min >= validation_ratio_max:
        raise RuntimeError(
            "Translate contract has invalid validation_ratio_min/validation_ratio_max bounds."
        )

    files = _sorted_chunk_files(in_dir, file_glob)
    if not files:
        raise RuntimeError(f"No chunk files found: {in_dir}/{file_glob}")
    if limit and limit > 0:
        files = files[:limit]

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
        "validation_ratio_min": validation_ratio_min,
        "validation_ratio_max": validation_ratio_max,
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
                ok_ratio, ratio = _ratio_ok(
                    len_in,
                    len_out,
                    min_ratio=validation_ratio_min,
                    max_ratio=validation_ratio_max,
                )
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


def _report_failure_reason(report: Dict, out_dir: Path) -> str | None:
    status = str(report.get("status", "")).strip()
    if status in {"error_preflight", "error", "failed"}:
        return f"report_status={status}"

    items = report.get("items") or []
    min_ratio = float(report.get("validation_ratio_min", 0.85))
    max_ratio = float(report.get("validation_ratio_max", 1.20))
    for item in items:
        output_path = item.get("output_path")
        if output_path:
            output_text = _read_text_safe(Path(output_path))
            if output_text and _has_structured_policy_violation(output_text):
                return "policy_block_structured"

        item_status = str(item.get("status", "")).strip()
        if item_status and item_status not in {"translated", "skipped_exists", "dry_run"}:
            return f"chunk_status={item_status}"

        response_status = str(item.get("response_status") or "").strip().lower()
        if response_status == "incomplete":
            return "response_status=incomplete"

        incomplete_reason = str(item.get("incomplete_reason") or "").strip().lower()
        if incomplete_reason in {"content_filter", "contentfilter"}:
            return f"incomplete_reason={incomplete_reason}"

        finish_reason = str(item.get("finish_reason") or "").strip().lower()
        if finish_reason in {"content_filter", "incomplete", "length"}:
            return f"finish_reason={finish_reason}"

        if item.get("truncated") is True:
            return "truncated"

        if item.get("structure_ok") is False:
            return "structure_ok_false"

        ratio = item.get("ratio")
        if isinstance(ratio, (int, float)):
            if ratio < min_ratio or ratio > max_ratio:
                return f"ratio_out_of_bounds={ratio:.3f}"

        in_lines = item.get("in_lines")
        out_lines = item.get("out_lines")
        if isinstance(in_lines, int) and isinstance(out_lines, int) and in_lines > 0:
            if out_lines < int(min_ratio * in_lines):
                return f"line_ratio_guard={out_lines}/{in_lines}"

        meta_path = item.get("meta_path")
        if meta_path:
            meta = _load_json_safe(Path(meta_path))
            if meta:
                response_status = str(meta.get("response_status") or "").strip().lower()
                if response_status == "incomplete":
                    return "response_status=incomplete"
                incomplete_reason = str(meta.get("incomplete_reason") or "").strip().lower()
                if incomplete_reason in {"content_filter", "contentfilter"}:
                    return f"incomplete_reason={incomplete_reason}"
                if meta.get("structure_ok") is False:
                    return "structure_ok_false"
                in_lines = meta.get("in_lines")
                out_lines = meta.get("out_lines")
                if isinstance(in_lines, int) and isinstance(out_lines, int) and in_lines > 0:
                    if out_lines < int(min_ratio * in_lines):
                        return f"line_ratio_guard={out_lines}/{in_lines}"
                ratio = meta.get("ratio")
                if isinstance(ratio, (int, float)) and (ratio < min_ratio or ratio > max_ratio):
                    return f"ratio_out_of_bounds={ratio:.3f}"
    return None


def _is_policy_block_reason(reason: str | None) -> bool:
    raw = (reason or "").strip().lower()
    if not raw:
        return False
    markers = (
        "content_filter",
        "contentfilter",
        "content filtered",
        "policy_violation",
        "policy_block_structured",
    )
    return any(marker in raw for marker in markers)


def _fallback_reason_label(reason: str | None) -> str:
    raw = (reason or "").strip().lower()
    if not raw:
        return "policy"
    if "content_filter" in raw or "content filtered" in raw:
        return "content_filter"
    if "policy" in raw:
        return "policy"
    return "policy"


def _read_text_safe(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _iter_json_blocks(text: str) -> List[str]:
    stripped = text.strip()
    candidates: List[str] = []
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    for block in re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.IGNORECASE | re.DOTALL):
        candidates.append(block.strip())
    return candidates


def _has_structured_policy_violation(text: str) -> bool:
    lowered = text.lower()
    if "[content_filter]" in lowered and "[/content_filter]" in lowered:
        return True

    for block in _iter_json_blocks(text):
        try:
            payload = json.loads(block)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue

        normalized = {str(k).lower(): v for k, v in payload.items()}
        if normalized.get("policy_violation") is True:
            return True
        if normalized.get("content_filter") is True:
            return True

        for key in ("error_type", "type", "reason", "status", "code"):
            value = normalized.get(key)
            if isinstance(value, str):
                raw = value.strip().lower()
                if "content_filter" in raw or "policy_violation" in raw:
                    return True
    return False


def _merge_refine_clean(
    out_dir: Path,
    suffix: str,
    *,
    book: str,
    target_lang: str,
    mode: str,
) -> tuple[Path, int, int]:
    pattern = f"ch_*_chunk_*.{suffix}.txt"
    files = sorted(out_dir.glob(pattern))
    if not files:
        files = sorted(out_dir.glob("ch_*_chunk_*.txt"))
    if not files:
        raise RuntimeError(f"No translated chunks found for merge_refine_clean in {out_dir}")

    parts: List[str] = []
    for fp in files:
        parts.append(fp.read_text(encoding="utf-8", errors="strict").rstrip("\n"))
    merged = "\n".join(parts).rstrip("\n") + "\n"
    out_path = canonical_artifact_path(out_dir, book, target_lang, mode)
    out_path.write_text(merged, encoding="utf-8", errors="strict")
    assert_valid_canonical_artifact(out_path)

    artifact_sha = sha256_file(out_path)
    input_hash = source_input_hash(files)
    write_canonical_meta(
        out_path,
        route=mode,
        artifact_sha256=artifact_sha,
        input_source_hash=input_hash,
        timestamp=_utc_now(),
    )

    # Compat output kept as non-canonical artifact; downstream consumers must ignore it.
    legacy_path = out_dir / "merge_refine_clean.txt"
    legacy_path.write_text(merged, encoding="utf-8", errors="strict")
    write_active_pointer(out_dir, book, target_lang, out_path.name)
    return out_path, len(merged.encode("utf-8")), len(files)


def _load_json_safe(path: Path) -> Dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_safe_report(out_dir: Path, payload: Dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "translate_safe_run_report.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_translate_safe(
    *,
    book: str | None = None,
    book_id: str | None = None,
    source_lang: str | None = None,
    target_lang: str | None = None,
    suffix: str | None = None,
    chunks_root: Path | None = None,
    translated_root: Path | None = None,
    chunk_dir: str | Path | None = None,
    out_dir: str | Path | None = None,
    resume: bool = True,
    dry_run: bool = True,
    contract_path: Path | str | None = None,
    contract: Dict | None = None,
    runs_root: Path | None = None,
    run_id: str | None = None,
    out_path: Path | None = None,
    limit: int = 0,
    fallback_temperature: float = 0.0,
    fallback_max_output_tokens: int = 8000,
    selected_mode: str = "automatic",
) -> Dict:
    """
    Automatic mode: official GPT-5.2 contract flow.
    If official path fails due to policy/content-filter reasons, fallback to target-aware agent route.
    Returns status: ok_official | ok_fallback | error_official | error_fallback | dry_run.
    """
    if book is None:
        book = book_id
    if chunk_dir is not None:
        chunk_dir_path = Path(chunk_dir)
        if chunk_dir_path.is_dir():
            if book is None:
                book = chunk_dir_path.parent.name
            if source_lang is None:
                source_lang = chunk_dir_path.name
            if chunks_root is None:
                chunks_root = chunk_dir_path.parents[1]
    if out_dir is not None:
        out_dir_path = Path(out_dir)
        if book is None:
            book = out_dir_path.parent.name
        if target_lang is None and suffix is None:
            target_lang = out_dir_path.name
        if translated_root is None:
            translated_root = out_dir_path.parents[1]
    if book is None:
        raise RuntimeError("book_id is required")
    if source_lang is None:
        source_lang = "en"
    if target_lang is None:
        target_lang = suffix or "en_modern"
    if chunks_root is None:
        chunks_root = Path("data/chunks")
    if translated_root is None:
        translated_root = Path("data/translated")

    selected_mode = normalize_mode(selected_mode, default="automatic")
    if selected_mode != "automatic":
        raise RuntimeError("run_translate_safe only supports selected_mode=automatic")

    book = normalize_book_code(str(book))
    source_lang = normalize_source_lang(source_lang, default="en")
    target_lang = normalize_lang_code(target_lang, default="en_modern")
    in_dir = Path(chunk_dir) if chunk_dir else (chunks_root / book / source_lang)
    out_dir = Path(out_dir) if out_dir else (translated_root / book / target_lang)
    official_report_path = out_dir / "translate_run_report.json"
    out_dir.mkdir(parents=True, exist_ok=True)

    from gaiden.openai_client import openai_healthcheck

    result: Dict[str, Any] = {
        "status": "error",
        "official_report": None,
        "official_error": None,
        "fallback_report": None,
        "merged_txt": None,
        "merged_len": None,
        "merged_count": None,
        "preflight_ok": False,
        "selected_mode": "automatic",
        "final_mode": "automatic",
        "effective_route": "automatic",
        "fallback_used": False,
        "fallback_reason": "none",
        "artifact_filename": None,
        "artifact_sha256": None,
        "artifact_meta_path": None,
        "errors": [],
        "exit_code": 3,
    }
    safe_report: Dict[str, Any] = {
        "schema": "gaiden_translate_safe_v2",
        "book_id": book,
        "lang": lang_token(target_lang),
        "selected_mode": "automatic",
        "final_mode": "automatic",
        "effective_route": "automatic",
        "preflight_ok": False,
        "fallback_used": False,
        "fallback_reason": "none",
        "artifact_filename": None,
        "artifact_sha256": None,
        "artifact_meta_path": None,
        "errors": [],
        "exit_code": None,
        "suffix": target_lang,
        "status": None,
        "error": None,
        "official": {"status": None, "report_path": None, "error": None},
        "fallback": {"used": False, "status": None, "report_path": None, "error": None},
        "final": {"merged_txt": None, "merged_len": None, "chunks": None},
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    failure_reason = None

    if dry_run:
        safe_report["status"] = "dry_run"
        safe_report["exit_code"] = 0
        safe_report["official"] = {"status": "skipped", "reason": "dry_run"}
        safe_report["fallback"] = {"used": False, "status": "skipped", "reason": "dry_run"}
        _write_safe_report(out_dir, safe_report)
        result["status"] = "dry_run"
        result["exit_code"] = 0
        return result

    ok, msg = openai_healthcheck()
    if not ok:
        safe_report["status"] = "error_preflight"
        safe_report["error"] = msg
        safe_report["errors"].append(msg)
        safe_report["exit_code"] = 2
        safe_report["official"] = {"status": "skipped", "reason": "preflight_failed"}
        safe_report["fallback"] = {"used": False, "status": "skipped", "reason": "preflight_failed"}
        _write_safe_report(out_dir, safe_report)
        result["errors"].append(msg)
        result["exit_code"] = 2
        raise RuntimeError(f"PRE-FLIGHT FAILED: {msg}")
    result["preflight_ok"] = True
    safe_report["preflight_ok"] = True

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
            limit=limit,
        )
        failure = None if dry_run else _report_failure_reason(report, out_dir)
        if failure:
            failure_reason = failure
            raise RuntimeError(f"OFFICIAL_VALIDATE_FAIL: {failure}")
        if official_report_path.exists():
            result["official_report"] = str(official_report_path)
            safe_report["official"]["report_path"] = str(official_report_path)
        safe_report["official"]["status"] = "ok_official"
        safe_report["status"] = "ok_official"
        print("[TRANSLATE_SAFE] official=OK")
        if not dry_run:
            merged_path, merged_len, merged_count = _merge_refine_clean(
                out_dir,
                target_lang,
                book=book,
                target_lang=target_lang,
                mode="automatic",
            )
            result["merged_txt"] = str(merged_path)
            result["merged_len"] = merged_len
            result["merged_count"] = merged_count
            result["artifact_filename"] = merged_path.name
            result["artifact_sha256"] = sha256_file(merged_path)
            result["artifact_meta_path"] = str(canonical_meta_path(merged_path))
            result["exit_code"] = 0
            safe_report["final"]["merged_txt"] = str(merged_path)
            safe_report["final"]["merged_len"] = merged_len
            safe_report["final"]["chunks"] = merged_count
            safe_report["artifact_filename"] = merged_path.name
            safe_report["artifact_sha256"] = result["artifact_sha256"]
            safe_report["artifact_meta_path"] = result["artifact_meta_path"]
            safe_report["exit_code"] = 0
            if out_path:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(merged_path, out_path)
            print(f"[TRANSLATE_SAFE] DONE merged={merged_path} bytes={merged_len}")
        _write_safe_report(out_dir, safe_report)
        result["status"] = "ok_official"
        return result
    except Exception as exc:
        err_str = repr(exc)
        result["official_error"] = err_str
        if official_report_path.exists():
            result["official_report"] = str(official_report_path)
            safe_report["official"]["report_path"] = str(official_report_path)
        if failure_reason is None:
            failure_reason = err_str
        if "APIConnectionError" in err_str or "gaierror" in err_str or "DNS_FAIL" in err_str:
            failure_reason = "NETWORK_DNS"
        result["errors"].append(str(failure_reason))
        safe_report["official"]["status"] = "error_official"
        safe_report["official"]["error"] = failure_reason
        safe_report["errors"].append(str(failure_reason))
        safe_report["status"] = "error_official"

    if dry_run:
        _write_safe_report(out_dir, safe_report)
        return result

    fallback_allowed = _is_policy_block_reason(failure_reason)
    if not fallback_allowed:
        safe_report["fallback"] = {
            "used": False,
            "status": "skipped",
            "reason": "official_failure_not_policy_block",
        }
        safe_report["fallback_used"] = False
        safe_report["fallback_reason"] = "none"
        safe_report["final_mode"] = "automatic"
        safe_report["effective_route"] = "automatic"
        safe_report["exit_code"] = 3
        result["status"] = "error_official"
        result["fallback_used"] = False
        result["fallback_reason"] = "none"
        result["final_mode"] = "automatic"
        result["effective_route"] = "automatic"
        result["exit_code"] = 3
        _write_safe_report(out_dir, safe_report)
        return result

    fallback_agent = resolve_agent_for_target(suffix=target_lang)
    print(f"[TRANSLATE_SAFE] official=FAILED reason={failure_reason} -> fallback={fallback_agent}")
    print("[TRANSLATE_SAFE] route=fallback_default")

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
        fallback_agent,
        "--suffix",
        target_lang,
        "--mode",
        "default",
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
        safe_report["fallback"]["report_path"] = str(fallback_report_path)

    if proc.returncode == 0 and fallback_report and fallback_report.get("status") == "ok":
        merged_txt = str(fallback_report.get("merged_txt") or "").strip()
        merged_path = Path(merged_txt) if merged_txt else None
        validation_error = None
        if not merged_path:
            validation_error = "missing merged_txt in fallback report"
        else:
            try:
                assert_valid_canonical_artifact(merged_path)
            except Exception as exc:
                validation_error = str(exc)

        if validation_error is None and merged_path is not None:
            result["status"] = "ok_fallback"
            result["merged_txt"] = str(merged_path)
            result["merged_len"] = fallback_report.get("merged_len")
            result["merged_count"] = fallback_report.get("merged_count")
            merged_name = merged_path.name
            result["artifact_filename"] = merged_name
            result["artifact_sha256"] = sha256_file(merged_path)
            result["artifact_meta_path"] = str(canonical_meta_path(merged_path))
            result["fallback_used"] = True
            result["fallback_reason"] = _fallback_reason_label(failure_reason)
            result["final_mode"] = "default"
            result["effective_route"] = "default"
            result["exit_code"] = 0
            safe_report["fallback"]["used"] = True
            safe_report["fallback"]["status"] = "ok_fallback"
            safe_report["status"] = "ok_fallback"
            safe_report["fallback_used"] = True
            safe_report["fallback_reason"] = result["fallback_reason"]
            safe_report["final_mode"] = "default"
            safe_report["effective_route"] = "default"
            safe_report["final"]["merged_txt"] = result["merged_txt"]
            safe_report["final"]["merged_len"] = result["merged_len"]
            safe_report["final"]["chunks"] = result["merged_count"]
            safe_report["artifact_filename"] = merged_name
            safe_report["artifact_sha256"] = result["artifact_sha256"]
            safe_report["artifact_meta_path"] = result["artifact_meta_path"]
            safe_report["exit_code"] = 0
            if out_path and result.get("merged_txt"):
                if merged_path.exists():
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(merged_path, out_path)
            _write_safe_report(out_dir, safe_report)
            print(f"[TRANSLATE_SAFE] DONE merged={result['merged_txt']} bytes={result['merged_len']}")
            return result

        result["fallback_error"] = {
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "validation_error": validation_error,
        }

    if not result.get("fallback_error"):
        result["fallback_error"] = {
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
        }
    result["errors"].append("FALLBACK_DEFAULT_FAILED")
    result["status"] = "error_fallback"
    result["fallback_used"] = True
    result["fallback_reason"] = _fallback_reason_label(failure_reason)
    result["final_mode"] = "default"
    result["effective_route"] = "default"
    result["exit_code"] = 4
    safe_report["fallback"]["used"] = True
    safe_report["fallback"]["status"] = "error_fallback"
    safe_report["fallback"]["error"] = json.dumps(result["fallback_error"], ensure_ascii=False)
    safe_report["fallback_used"] = True
    safe_report["fallback_reason"] = result["fallback_reason"]
    safe_report["final_mode"] = "default"
    safe_report["effective_route"] = "default"
    safe_report["errors"].append("FALLBACK_DEFAULT_FAILED")
    safe_report["status"] = "error_fallback"
    safe_report["exit_code"] = 4
    _write_safe_report(out_dir, safe_report)
    return result
