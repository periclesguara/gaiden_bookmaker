from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gaiden.application.agents.stage_resolver import resolve_agent_for_ui_stage
from gaiden.application.agents.stages.modernize_en_us_2026 import run_modernize_en_us_2026


def _chunk_paths(chunk_dir: Path) -> list[Path]:
    return sorted(path for path in chunk_dir.glob("*.txt") if path.is_file())


def _merge_outputs(out_dir: Path, language: str) -> Path:
    chunks = sorted(
        path
        for path in out_dir.glob("*.txt")
        if not path.name.startswith("merged_") and not path.name.startswith("merge_")
    )
    merged_text = "\n\n".join(path.read_text(encoding="utf-8").rstrip() for path in chunks)
    merged_path = out_dir / f"merged_{language}.txt"
    merged_path.write_text(merged_text.rstrip() + "\n", encoding="utf-8")
    return merged_path


def run_translate_en_us_modernize(
    *,
    book_id: str,
    chunk_dir: str | Path,
    out_dir: str | Path,
    overwrite: bool = False,
    limit: int = 0,
) -> dict[str, Any]:
    resolution = resolve_agent_for_ui_stage("translate", "en_us")
    chunk_dir_path = Path(chunk_dir)
    out_dir_path = Path(out_dir)
    if not chunk_dir_path.exists():
        raise FileNotFoundError(f"Translate source chunk dir not found: {chunk_dir_path}")
    out_dir_path.mkdir(parents=True, exist_ok=True)

    print("[Gaiden Agents] UI stage: translate")
    print("[Gaiden Agents] Target language: en_us")
    print(f"[Gaiden Agents] Resolved internal stage: {resolution['stage']}")
    print(f"[Gaiden Agents] Resolved agent: {resolution['agent_id']}")
    print("[Gaiden Agents] Model: gpt-5.4")
    print(f"[Gaiden Agents] Contract: {resolution['contract_path']}")

    chunks = _chunk_paths(chunk_dir_path)
    if limit and limit > 0:
        chunks = chunks[:limit]
    if not chunks:
        raise RuntimeError(f"NO_CHUNKS: nothing matched {chunk_dir_path}/*.txt")

    items: list[dict[str, Any]] = []
    for chunk_path in chunks:
        target_path = out_dir_path / chunk_path.name
        report = run_modernize_en_us_2026(
            {
                "job_id": f"translate_en_us_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{chunk_path.stem}",
                "book_id": book_id,
                "ui_stage": "translate",
                "stage": resolution["stage"],
                "language": resolution["language"],
                "target_language": "en_us",
                "agent_id": resolution["agent_id"],
                "input": {"source_path": str(chunk_path)},
                "output": {"target_path": str(target_path), "overwrite": overwrite},
            }
        )
        items.append(
            {
                "chunk": chunk_path.name,
                "out_txt": str(target_path),
                "status": report.get("status"),
                "validation": report.get("validation"),
                "audit_path": report.get("audit_path"),
            }
        )
        if report.get("status") not in {"passed", "skipped"}:
            raise RuntimeError(f"Translate EN-US modernization failed for {chunk_path.name}: {report}")

    merged_path = _merge_outputs(out_dir_path, "en_us")
    run_report = {
        "schema": "gaiden_translate_en_us_modernize_v1",
        "ui_stage": "translate",
        "resolved_stage": resolution["stage"],
        "language": resolution["language"],
        "target_language": "en_us",
        "agent_id": resolution["agent_id"],
        "model": "gpt-5.4",
        "contract_path": resolution["contract_path"],
        "book_id": book_id,
        "chunk_dir": str(chunk_dir_path),
        "out_dir": str(out_dir_path),
        "merged_txt": str(merged_path),
        "count": len(items),
        "items": items,
        "status": "ok",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir_path / "agent_translate_run_report.json").write_text(
        json.dumps(run_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return run_report
