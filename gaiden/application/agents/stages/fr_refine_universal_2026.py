from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gaiden.application.agents.contracts import (
    load_agent_contract,
    validate_refine_contract_for_language,
)
from gaiden.openai_client import call_agent_text


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _audit_path(job: dict[str, Any], source_path: Path) -> Path:
    book_id = str(job.get("book_id") or "generic_book")
    language = str(job.get("language") or "fr")
    stage = str(job.get("stage") or "refine")
    return Path("docs/audit/agent_runs") / book_id / language / stage / f"{source_path.stem}.run.json"


def _write_audit(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_fr_refine_universal_2026(job: dict[str, Any]) -> dict[str, Any]:
    source_path = Path(job["input"]["source_path"])
    target_path = Path(job["output"]["target_path"])
    overwrite = bool(job.get("output", {}).get("overwrite", False))
    audit_path = _audit_path(job, source_path)
    created_at = datetime.now(timezone.utc).isoformat()

    if target_path.exists() and not overwrite:
        report = {
            "job_id": job.get("job_id"),
            "book_id": job.get("book_id"),
            "metadata": job.get("metadata") or {},
            "ui_stage": job.get("ui_stage", "refine"),
            "stage": "refine",
            "resolved_stage": "refine",
            "language": "fr",
            "target_language": "fr",
            "agent_id": "fr_refine_universal_2026",
            "status": "skipped",
            "reason": "target_exists_overwrite_false",
            "input_path": str(source_path),
            "output_path": str(target_path),
            "audit_path": str(audit_path),
            "created_at": created_at,
        }
        _write_audit(audit_path, report)
        return report

    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    contract = load_agent_contract("fr_refine_universal_2026")
    validate_refine_contract_for_language("fr", contract)
    source_text = source_path.read_text(encoding="utf-8")
    system_prompt = str(contract.get("system_prompt") or "").strip()
    if not system_prompt:
        raise ValueError("FR_REFINE_UNIVERSAL contract requires system_prompt.")
    model = str(contract.get("model") or "").strip()
    temperature = float(contract.get("temperature", 0.25))
    output_text = ""
    errors: list[str] = []
    usage = {"input_tokens": None, "output_tokens": None, "total_tokens": None}

    try:
        output_text = call_agent_text(
            agent_name="fr_refine_universal_2026",
            text=source_text,
            model=model,
            temperature=temperature,
            max_output_tokens=int(job.get("max_output_tokens") or 8000),
            system_prompt=system_prompt,
        ).strip()
        if not output_text:
            raise RuntimeError("FR_REFINE_UNIVERSAL returned empty output.")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(output_text.rstrip() + "\n", encoding="utf-8")
        status = "passed"
        validation_report = {
            "status": "passed",
            "validators": [
                {
                    "id": "non_empty_output",
                    "status": "passed",
                    "message": "Output exists.",
                }
            ],
        }
    except Exception as exc:
        status = "failed"
        errors.append(str(exc))
        validation_report = {
            "status": "failed",
            "validators": [
                {
                    "id": "fr_refine_runtime",
                    "status": "failed",
                    "message": str(exc),
                }
            ],
        }

    audit = {
        "job_id": job.get("job_id"),
        "book_id": job.get("book_id"),
        "metadata": job.get("metadata") or {},
        "ui_stage": job.get("ui_stage", "refine"),
        "stage": "refine",
        "resolved_stage": "refine",
        "language": "fr",
        "target_language": "fr",
        "agent_id": "fr_refine_universal_2026",
        "agent_name": contract.get("agent_name"),
        "contract_name": contract.get("contract_name"),
        "contract_version": contract.get("contract_version"),
        "model": model,
        "input_path": str(source_path),
        "output_path": str(target_path),
        "input_sha256": _sha256_text(source_text),
        "output_sha256": _sha256_text(output_text) if output_text else None,
        "validation": validation_report,
        "usage": usage,
        "cost": {"currency": "USD", "estimated": None},
        "status": status,
        "errors": errors,
        "audit_path": str(audit_path),
        "created_at": created_at,
    }
    _write_audit(audit_path, audit)
    return audit
