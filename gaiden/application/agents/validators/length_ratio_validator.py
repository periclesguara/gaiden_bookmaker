from __future__ import annotations

from typing import Any


def validate_length_ratio(source_text: str, output_text: str, validator_contract: dict[str, Any]) -> dict[str, Any]:
    source_length = len(source_text)
    output_length = len(output_text)
    ratio = (output_length / source_length) if source_length else 0.0
    min_ratio = float(validator_contract.get("min_ratio", 0.0))
    max_ratio = float(validator_contract.get("max_ratio", 999.0))
    suspicious = source_length == 0 or ratio < min_ratio or ratio > max_ratio

    if suspicious and validator_contract.get("fail_on_match"):
        status = "failed"
    elif suspicious and validator_contract.get("manual_review_on_match", True):
        status = "manual_review"
    else:
        status = "passed"

    template = validator_contract.get("failure_message_template") or (
        "Suspicious length ratio: {{ratio}}."
    )
    message = None
    if suspicious:
        message = (
            template.replace("{{source_length}}", str(source_length))
            .replace("{{output_length}}", str(output_length))
            .replace("{{ratio}}", f"{ratio:.4f}")
        )

    return {
        "id": validator_contract.get("id"),
        "status": status,
        "matches": [{"source_length": source_length, "output_length": output_length, "ratio": ratio}]
        if suspicious
        else [],
        "message": message,
    }
