from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gaiden.application.agents.contracts import load_agent_contract, load_json_contract, resolve_agent
from gaiden.application.agents.prompt_builder import build_messages
from gaiden.application.agents.validators import run_validators
from gaiden.infrastructure.openai import responses_client


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bool_arg(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError(f"Invalid boolean value: {value}")


def _audit_path(job: dict[str, Any], source_path: Path) -> Path:
    book_id = str(job["book_id"])
    language = str(job.get("language") or "en_us")
    stage = str(job.get("stage") or "modernize")
    chunk_name = source_path.stem
    return Path("docs/audit/agent_runs") / book_id / language / stage / f"{chunk_name}.run.json"


def _write_audit(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_contract_set(agent_contract: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    refs = agent_contract.get("contract_refs") or {}
    language_contract = load_json_contract(refs["language"])
    stage_contract = load_json_contract(refs["stage"])
    validator_contracts = [load_json_contract(path) for path in refs.get("validators", [])]
    return language_contract, stage_contract, validator_contracts


def _retry_messages(system_prompt: str, validation_report: dict[str, Any], previous_output: str) -> list[dict[str, str]]:
    failures = [
        item
        for item in validation_report.get("validators", [])
        if item.get("status") == "failed"
    ]
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "developer",
            "content": (
                "The previous output failed validation.\n"
                "Fix only the validation issues.\n"
                "Do not summarize.\n"
                "Do not explain.\n"
                "Do not add notes.\n"
                "Return only the corrected text.\n\n"
                "VALIDATION FAILURES:\n"
                f"{json.dumps(failures, ensure_ascii=False, indent=2)}\n\n"
                "TEXT TO CORRECT:\n"
                f"{previous_output}"
            ),
        },
        {"role": "user", "content": "Return only the corrected text."},
    ]


def _should_retry(validation_report: dict[str, Any], output_text: str) -> bool:
    if not output_text.strip():
        return True
    return any(item.get("status") == "failed" for item in validation_report.get("validators", []))


def _usage_empty() -> dict[str, Any]:
    return {"input_tokens": None, "output_tokens": None, "total_tokens": None}


def run_modernize_en_us_2026(job: dict[str, Any]) -> dict[str, Any]:
    source_path = Path(job["input"]["source_path"])
    target_path = Path(job["output"]["target_path"])
    overwrite = bool(job.get("output", {}).get("overwrite", False))
    audit_path = _audit_path(job, source_path)
    created_at = datetime.now(timezone.utc).isoformat()

    if target_path.exists() and not overwrite:
        report = {
            "job_id": job.get("job_id"),
            "book_id": job.get("book_id"),
            "stage": job.get("stage", "modernize"),
            "language": job.get("language", "en_us"),
            "agent_id": job.get("agent_id", "modernize_en_us_2026"),
            "status": "skipped",
            "reason": "target_exists_overwrite_false",
            "input_path": str(source_path),
            "output_path": str(target_path),
            "created_at": created_at,
        }
        _write_audit(audit_path, report)
        return report

    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")
    source_text = source_path.read_text(encoding="utf-8")

    if job.get("agent_id"):
        agent_contract = load_agent_contract(str(job["agent_id"]))
    else:
        agent_contract = resolve_agent(str(job.get("stage", "modernize")), str(job.get("language", "en_us")))
    language_contract, stage_contract, validator_contracts = _load_contract_set(agent_contract)

    engine = agent_contract.get("engine") or {}
    model = str(engine.get("default_model") or "gpt-5.4")
    models_to_try = [model] + [str(item) for item in engine.get("fallback_models", []) if str(item) != model]
    temperature = float(engine.get("temperature", 0.2))
    reasoning_effort = str(engine.get("reasoning_effort", "medium"))
    validation_policy = agent_contract.get("validation") or {}
    max_retries = int(validation_policy.get("max_retries", 0))
    retry_on_failure = bool(validation_policy.get("retry_on_failure", False))

    messages = build_messages(agent_contract, language_contract, stage_contract, validator_contracts, source_text)
    system_prompt = messages[0]["content"]
    output_text = ""
    used_model = model
    usage = _usage_empty()
    validation_report: dict[str, Any] = {"status": "failed", "validators": []}
    errors: list[str] = []
    retries = 0
    attempt_messages = messages

    try:
        for attempt_index in range(max_retries + 1):
            call_result = None
            last_error: Exception | None = None
            for current_model in models_to_try:
                try:
                    call_result = responses_client.run_responses(
                        attempt_messages,
                        model=current_model,
                        temperature=temperature,
                        reasoning_effort=reasoning_effort,
                    )
                    used_model = str(call_result.get("model") or current_model)
                    break
                except Exception as exc:
                    last_error = exc
                    errors.append(f"model {current_model} failed: {exc}")
            if call_result is None:
                raise RuntimeError("All configured models failed.") from last_error

            output_text = str(call_result.get("output_text") or "").strip()
            usage = call_result.get("usage") or _usage_empty()
            if output_text:
                validation_report = run_validators(source_text, output_text, validator_contracts)
            else:
                validation_report = {
                    "status": "failed",
                    "validators": [
                        {
                            "id": "empty_output",
                            "status": "failed",
                            "matches": [],
                            "message": "The model returned empty output.",
                        }
                    ],
                }

            if validation_report["status"] in {"passed", "manual_review"}:
                break
            if not retry_on_failure or attempt_index >= max_retries or not _should_retry(validation_report, output_text):
                break
            retries += 1
            attempt_messages = _retry_messages(system_prompt, validation_report, output_text)

        status = "passed" if validation_report["status"] in {"passed", "manual_review"} else "failed"
        if status == "passed":
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(output_text.rstrip() + "\n", encoding="utf-8")
    except Exception as exc:
        status = "failed"
        errors.append(str(exc))

    audit = {
        "job_id": job.get("job_id"),
        "book_id": job.get("book_id"),
        "stage": job.get("stage", "modernize"),
        "language": job.get("language", "en_us"),
        "agent_id": agent_contract.get("id") if "agent_contract" in locals() else job.get("agent_id"),
        "agent_version": agent_contract.get("version") if "agent_contract" in locals() else None,
        "model": used_model,
        "input_path": str(source_path),
        "output_path": str(target_path),
        "input_sha256": _sha256_text(source_text) if "source_text" in locals() else None,
        "output_sha256": _sha256_text(output_text) if output_text else None,
        "validation": validation_report,
        "retries": retries,
        "usage": usage,
        "cost": {"currency": "USD", "estimated": None},
        "status": status,
        "errors": errors,
        "created_at": created_at,
    }
    _write_audit(audit_path, audit)
    if status == "failed":
        return audit
    return audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Gaiden Modernize EN-US 2026 internal agent.")
    parser.add_argument("--book-id", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--overwrite", type=_bool_arg, default=False)
    parser.add_argument("--job-id", default=None)
    args = parser.parse_args(argv)

    job = {
        "job_id": args.job_id or f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "book_id": args.book_id,
        "stage": "modernize",
        "language": "en_us",
        "agent_id": "modernize_en_us_2026",
        "input": {"source_path": args.source},
        "output": {"target_path": args.target, "overwrite": args.overwrite},
    }
    report = run_modernize_en_us_2026(job)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
