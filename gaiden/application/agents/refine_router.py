from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gaiden.application.agents.stage_resolver import normalize_target_language_alias, resolve_agent_for_ui_stage
from gaiden.application.agents.stages.refine_en_us_2026 import run_refine_en_us_2026


def run_refine(
    *,
    book_id: str,
    target_language: str,
    source_path: str | Path,
    target_path: str | Path,
    overwrite: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_language = normalize_target_language_alias(target_language)
    if normalized_language != "en_us":
        raise LookupError(f"No internal refine agent configured for target_language={target_language}")

    resolution = resolve_agent_for_ui_stage("refine", normalized_language)

    print("[Gaiden Agents] UI stage: refine")
    print(f"[Gaiden Agents] Target language: {normalized_language}")
    print(f"[Gaiden Agents] Resolved internal stage: {resolution['stage']}")
    print(f"[Gaiden Agents] Resolved agent: {resolution['agent_id']}")
    print("[Gaiden Agents] Model: gpt-5.4")
    print(f"[Gaiden Agents] Contract: {resolution['contract_path']}")

    job = {
            "job_id": f"refine_en_us_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            "book_id": book_id,
            "ui_stage": "refine",
            "stage": resolution["stage"],
            "language": resolution["language"],
            "target_language": normalized_language,
            "agent_id": resolution["agent_id"],
            "input": {"source_path": str(source_path)},
            "output": {"target_path": str(target_path), "overwrite": overwrite},
    }
    if metadata:
        job["metadata"] = dict(metadata)
        if metadata.get("run_id"):
            job["job_id"] = f"{metadata['run_id']}__{metadata.get('source_chunk_id', Path(source_path).stem)}"
    return run_refine_en_us_2026(job)
