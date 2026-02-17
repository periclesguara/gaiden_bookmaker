#!/usr/bin/env python3
"""
Force-translate chunks via an OpenAI Agent (default: ALAMAGUEDERAZ).

This bypasses any gpt-5.2 translate path and sends each chunk directly to the agent.
Outputs are written into out_dir, preserving per-chunk filenames, plus a run report.

Example:
PYTHONPATH=. python gaiden/tools/agent_translate_default.py \
  --book-id book_0003 \
  --chunk-dir data/chunks/book_0003/en \
  --out-dir data/translated/book_0003/en_modern \
  --agent ALAMAGUEDERAZ \
  --suffix en_modern \
  --limit 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

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

CHUNK_GLOB = "ch_*_chunk_*.txt"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="strict")


def _write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8", errors="strict")


def _write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_len(s: str) -> int:
    return len(s or "")


def _merge_outputs(
    out_dir: Path,
    suffix: str,
    *,
    book_id: str,
    mode: str,
) -> tuple[Path, int, int]:
    files = sorted(out_dir.glob(f"ch_*_chunk_*.{suffix}.txt"))
    if not files:
        raise RuntimeError(f"NO_TRANSLATED_CHUNKS: nothing matched {out_dir}/ch_*_chunk_*.{suffix}.txt")
    parts: List[str] = []
    for fp in files:
        parts.append(fp.read_text(encoding="utf-8", errors="strict").rstrip("\n"))
    merged = "\n".join(parts).rstrip("\n") + "\n"
    out_path = canonical_artifact_path(out_dir, book_id, suffix, mode)
    out_path.write_text(merged, encoding="utf-8", errors="strict")
    assert_valid_canonical_artifact(out_path)

    artifact_sha = sha256_file(out_path)
    input_hash = source_input_hash(files)
    write_canonical_meta(
        out_path,
        route=mode,
        artifact_sha256=artifact_sha,
        input_source_hash=input_hash,
    )

    # Compat output kept as non-canonical artifact; downstream consumers must ignore it.
    legacy_path = out_dir / "merge_refine_clean.txt"
    legacy_path.write_text(merged, encoding="utf-8", errors="strict")
    write_active_pointer(out_dir, book_id, suffix, out_path.name)
    return out_path, len(merged.encode("utf-8")), len(files)


def _call_agent(agent_name: str, text: str, *, temperature: float = 0.4, max_output_tokens: int = 8000) -> Tuple[str, Dict[str, Any]]:
    """
    Calls your repo's agent helper if available.
    We assume your project already has an OpenAI client wrapper like gaiden/openai_client.py.
    """
    meta: Dict[str, Any] = {
        "agent_name": agent_name,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }

    # Preferred: your existing helper
    try:
        from gaiden.openai_client import call_agent_text  # type: ignore

        # Your repo previously used a signature like:
        # call_agent_text(agent_name=..., text=..., model=..., temperature=..., max_output_tokens=..., system_prompt=...)
        # Agents already contain instructions, so we only pass agent_name + text (plus temp/tokens).
        out = call_agent_text(
            agent_name=agent_name,
            text=text,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        meta["call_impl"] = "gaiden.openai_client.call_agent_text"
        return out, meta

    except Exception as e:
        meta["call_impl"] = "missing_or_failed_gaiden_helper"
        meta["call_error"] = repr(e)
        raise RuntimeError(
            "SHIM_MISSING: Could not import/use gaiden.openai_client.call_agent_text. "
            f"Root error: {e!r}"
        ) from e


def _chunk_paths(chunk_dir: Path) -> List[Path]:
    return sorted(chunk_dir.glob(CHUNK_GLOB))


def run_agent_translate(
    *,
    book_id: str,
    chunk_dir: str | Path,
    out_dir: str | Path,
    suffix: str,
    mode: str = "default",
    temperature: float = 0.4,
    max_output_tokens: int = 8000,
    limit: int = 0,
    agent: str | None = None,
) -> Dict[str, Any]:
    chunk_dir = Path(chunk_dir)
    out_dir = Path(out_dir)
    book_id = normalize_book_code(book_id)
    mode = normalize_mode(mode, default="default")
    agent = agent or os.getenv("GAIDEN_DEFAULT_TRANSLATE_AGENT", "ALAMAGUEDERAZ")

    out_dir.mkdir(parents=True, exist_ok=True)

    from gaiden.openai_client import openai_healthcheck

    ok, err = openai_healthcheck()
    if not ok:
        run_report: Dict[str, Any] = {
            "schema": "gaiden_translate_default_v2",
            "ts_start": _now_iso(),
            "ts_end": _now_iso(),
            "book_id": book_id,
            "lang": lang_token(suffix),
        "selected_mode": mode,
        "final_mode": mode,
        "effective_route": mode,
        "preflight_ok": False,
        "fallback_used": False,
        "fallback_reason": "none",
        "artifact_filename": None,
        "artifact_sha256": None,
        "artifact_meta_path": None,
        "errors": [err or "OPENAI_PREFLIGHT_FAILED"],
        "exit_code": 2,
            "agent": agent,
            "chunk_dir": str(chunk_dir),
            "out_dir": str(out_dir),
            "suffix": suffix,
            "count": 0,
            "items": [],
            "status": "error_preflight",
            "error": err or "OPENAI_PREFLIGHT_FAILED",
        }
        _write_json(out_dir / "agent_translate_run_report.json", run_report)
        raise RuntimeError(err or "OPENAI_PREFLIGHT_FAILED")

    chunks = _chunk_paths(chunk_dir)
    if not chunks:
        raise RuntimeError(f"NO_CHUNKS: nothing matched {chunk_dir}/{CHUNK_GLOB}")

    if limit and limit > 0:
        chunks = chunks[:limit]

    run_report: Dict[str, Any] = {
        "schema": "gaiden_translate_default_v2",
        "ts_start": _now_iso(),
        "book_id": book_id,
        "lang": lang_token(suffix),
        "selected_mode": mode,
        "final_mode": mode,
        "effective_route": mode,
        "preflight_ok": True,
        "fallback_used": False,
        "fallback_reason": "none",
        "artifact_filename": None,
        "artifact_sha256": None,
        "artifact_meta_path": None,
        "errors": [],
        "exit_code": None,
        "agent": agent,
        "chunk_dir": str(chunk_dir),
        "out_dir": str(out_dir),
        "suffix": suffix,
        "count": len(chunks),
        "items": [],
        "status": "running",
    }

    for p in chunks:
        in_text = _read_text(p)
        in_len = _safe_len(in_text)

        base = p.stem  # e.g. ch_001_chunk_001
        out_txt = out_dir / f"{base}.{suffix}.txt"
        out_meta = out_dir / f"{base}.{suffix}.meta.json"

        item: Dict[str, Any] = {
            "chunk": p.name,
            "in_len": in_len,
            "out_txt": str(out_txt),
            "out_meta": str(out_meta),
            "status": "running",
            "ts": _now_iso(),
        }

        try:
            out_text, meta = _call_agent(
                agent,
                in_text,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
            out_len = _safe_len(out_text)
            ratio = (out_len / in_len) if in_len else None
            if ratio is not None and ratio < 0.85:
                raise RuntimeError(f"TRUNCATION_OR_SUMMARY: ratio={ratio:.3f}")
            item.update(
                {
                    "status": "ok",
                    "out_len": out_len,
                    "ratio": ratio,
                    "agent_meta": meta,
                }
            )
            _write_text(out_txt, out_text)
            _write_json(out_meta, item)
        except Exception as e:
            item.update({"status": "error", "error": repr(e)})
            _write_json(out_meta, item)
            run_report["items"].append(item)
            run_report["status"] = "error"
            run_report["errors"].append(repr(e))
            run_report["exit_code"] = 3
            run_report["ts_end"] = _now_iso()
            _write_json(out_dir / "agent_translate_run_report.json", run_report)
            raise

        run_report["items"].append(item)

    run_report["status"] = "ok"
    run_report["ts_end"] = _now_iso()
    try:
        merged_path, merged_len, merged_count = _merge_outputs(
            out_dir,
            suffix,
            book_id=book_id,
            mode=mode,
        )
    except Exception as exc:
        run_report["status"] = "error"
        run_report["errors"].append(repr(exc))
        run_report["exit_code"] = 3
        run_report["ts_end"] = _now_iso()
        _write_json(out_dir / "agent_translate_run_report.json", run_report)
        raise
    run_report["merged_txt"] = str(merged_path)
    run_report["merged_len"] = merged_len
    run_report["merged_count"] = merged_count
    run_report["artifact_filename"] = merged_path.name
    run_report["artifact_sha256"] = sha256_file(merged_path)
    run_report["artifact_meta_path"] = str(canonical_meta_path(merged_path))
    run_report["exit_code"] = 0
    _write_json(out_dir / "agent_translate_run_report.json", run_report)
    print(f"[MERGE] wrote {merged_path} bytes={merged_len} chunks={merged_count}")
    return run_report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book-id", required=True, help="e.g. book_0003")
    ap.add_argument("--chunk-dir", required=True, help="e.g. data/chunks/book_0003/en")
    ap.add_argument("--out-dir", required=True, help="e.g. data/translated/book_0003/en_modern")
    ap.add_argument("--agent", default=os.getenv("GAIDEN_DEFAULT_TRANSLATE_AGENT", "ALAMAGUEDERAZ"))
    ap.add_argument("--suffix", default="en_modern", help="suffix for output filenames")
    ap.add_argument("--mode", default="default", choices=["default"], help="translate mode")
    ap.add_argument("--limit", type=int, default=0, help="0 = all chunks")
    ap.add_argument("--temperature", type=float, default=0.4)
    ap.add_argument("--max-output-tokens", type=int, default=8000)
    args = ap.parse_args()

    try:
        run_agent_translate(
            book_id=args.book_id,
            chunk_dir=args.chunk_dir,
            out_dir=args.out_dir,
            suffix=args.suffix,
            mode=args.mode,
            temperature=args.temperature,
            max_output_tokens=args.max_output_tokens,
            limit=args.limit,
            agent=args.agent,
        )
    except Exception:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
