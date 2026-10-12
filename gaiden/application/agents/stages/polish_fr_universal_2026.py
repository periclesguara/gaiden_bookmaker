from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gaiden.application.agents.contracts import (
    load_agent_contract,
    validate_polish_contract_for_language,
)
from gaiden.application.agents.stages.modernize_en_us_2026 import _bool_arg
from gaiden.openai_client import call_agent_text


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _audit_path(job: dict[str, Any], source_path: Path) -> Path:
    book_id = str(job.get("book_id") or "generic_book")
    language = str(job.get("language") or "fr")
    stage = str(job.get("stage") or "polish")
    return Path("docs/audit/agent_runs") / book_id / language / stage / f"{source_path.stem}.run.json"


def _write_audit(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_user_prompt(contract: dict[str, Any], source_text: str) -> str:
    prompt_template = contract.get("prompt_template") or {}
    user_prompt = str(prompt_template.get("user") or "").strip()
    if not user_prompt:
        return source_text
    return user_prompt.replace("{{text}}", source_text).replace("{{source_text}}", source_text)


def run_polish_fr_universal_2026(job: dict[str, Any]) -> dict[str, Any]:
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
            "ui_stage": job.get("ui_stage", "polish"),
            "stage": "polish",
            "resolved_stage": "polish",
            "language": "fr",
            "target_language": "fr",
            "agent_id": "polish_fr_universal_2026",
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

    contract = load_agent_contract("polish_fr_universal_2026")
    validate_polish_contract_for_language("fr", contract)
    source_text = source_path.read_text(encoding="utf-8")
    system_prompt = str(contract.get("system_prompt") or "").strip()
    if not system_prompt:
        raise ValueError("POLISH_FR contract requires system_prompt.")
    model = str(contract.get("model") or "").strip()
    temperature = float(contract.get("temperature", 0.35))
    max_output_tokens = int(job.get("max_output_tokens") or contract.get("max_output_tokens") or 12000)
    prompt_text = _build_user_prompt(contract, source_text)
    output_text = ""
    errors: list[str] = []
    usage = {"input_tokens": None, "output_tokens": None, "total_tokens": None}

    try:
        output_text = call_agent_text(
            agent_name="polish_fr_universal_2026",
            text=prompt_text,
            model=model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            system_prompt=system_prompt,
        ).strip()
        if not output_text:
            raise RuntimeError("POLISH_FR returned empty output.")
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
                    "id": "fr_polish_runtime",
                    "status": "failed",
                    "message": str(exc),
                }
            ],
        }

    audit = {
        "job_id": job.get("job_id"),
        "book_id": job.get("book_id"),
        "metadata": job.get("metadata") or {},
        "ui_stage": job.get("ui_stage", "polish"),
        "stage": "polish",
        "resolved_stage": "polish",
        "language": "fr",
        "target_language": "fr",
        "agent_id": "polish_fr_universal_2026",
        "agent_name": contract.get("agent_name"),
        "contract_name": contract.get("contract_name"),
        "contract_version": contract.get("version"),
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Gaiden POLISH_FR universal internal agent.")
    parser.add_argument("--book-id", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--overwrite", type=_bool_arg, default=False)
    parser.add_argument("--job-id", default=None)
    args = parser.parse_args(argv)

    job = {
        "job_id": args.job_id or f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "book_id": args.book_id,
        "ui_stage": "polish",
        "stage": "polish",
        "language": "fr",
        "target_language": "fr",
        "agent_id": "polish_fr_universal_2026",
        "input": {"source_path": args.source},
        "output": {"target_path": args.target, "overwrite": args.overwrite},
    }
    report = run_polish_fr_universal_2026(job)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
