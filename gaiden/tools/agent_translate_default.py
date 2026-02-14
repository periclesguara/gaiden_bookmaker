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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def _merge_outputs(out_dir: Path, suffix: str) -> tuple[Path, int, int]:
    files = sorted(out_dir.glob(f"ch_*_chunk_*.{suffix}.txt"))
    parts: List[str] = []
    for fp in files:
        parts.append(fp.read_text(encoding="utf-8", errors="strict").rstrip("\n"))
    merged = "\n".join(parts).rstrip("\n") + "\n"
    out_path = out_dir / "merge_refine_clean.txt"
    out_path.write_text(merged, encoding="utf-8", errors="strict")
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book-id", required=True, help="e.g. book_0003")
    ap.add_argument("--chunk-dir", required=True, help="e.g. data/chunks/book_0003/en")
    ap.add_argument("--out-dir", required=True, help="e.g. data/translated/book_0003/en_modern")
    ap.add_argument("--agent", default=os.getenv("GAIDEN_DEFAULT_TRANSLATE_AGENT", "ALAMAGUEDERAZ"))
    ap.add_argument("--suffix", default="en_modern", help="suffix for output filenames")
    ap.add_argument("--limit", type=int, default=0, help="0 = all chunks")
    ap.add_argument("--temperature", type=float, default=0.4)
    ap.add_argument("--max-output-tokens", type=int, default=8000)
    args = ap.parse_args()

    book_id = args.book_id
    chunk_dir = Path(args.chunk_dir)
    out_dir = Path(args.out_dir)
    agent = args.agent
    suffix = args.suffix

    out_dir.mkdir(parents=True, exist_ok=True)

    from gaiden.openai_client import openai_healthcheck

    ok, err = openai_healthcheck()
    if not ok:
        run_report: Dict[str, Any] = {
            "schema": "gaiden_agent_translate_run_v1",
            "ts_start": _now_iso(),
            "ts_end": _now_iso(),
            "book_id": book_id,
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
        return 2

    chunks = _chunk_paths(chunk_dir)
    if not chunks:
        raise SystemExit(f"NO_CHUNKS: nothing matched {chunk_dir}/{CHUNK_GLOB}")

    if args.limit and args.limit > 0:
        chunks = chunks[: args.limit]

    run_report: Dict[str, Any] = {
        "schema": "gaiden_agent_translate_run_v1",
        "ts_start": _now_iso(),
        "book_id": book_id,
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
                temperature=args.temperature,
                max_output_tokens=args.max_output_tokens,
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
            run_report["ts_end"] = _now_iso()
            _write_json(out_dir / "agent_translate_run_report.json", run_report)
            return 2

        run_report["items"].append(item)

    run_report["status"] = "ok"
    run_report["ts_end"] = _now_iso()
    merged_path, merged_len, merged_count = _merge_outputs(out_dir, suffix)
    run_report["merged_txt"] = str(merged_path)
    run_report["merged_len"] = merged_len
    run_report["merged_count"] = merged_count
    _write_json(out_dir / "agent_translate_run_report.json", run_report)
    print(f"[MERGE] wrote {merged_path} bytes={merged_len} chunks={merged_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
