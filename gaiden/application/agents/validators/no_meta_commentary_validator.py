from __future__ import annotations

from typing import Any


def _normalize(value: str, case_sensitive: bool) -> str:
    stripped = value.strip()
    return stripped if case_sensitive else stripped.lower()


def validate_no_meta_commentary(source_text: str, output_text: str, validator_contract: dict[str, Any]) -> dict[str, Any]:
    del source_text
    case_sensitive = bool(validator_contract.get("case_sensitive"))
    normalized = _normalize(output_text, case_sensitive)
    full_text = output_text if case_sensitive else output_text.lower()
    matches: list[str] = []

    for prefix in validator_contract.get("forbidden_prefixes") or []:
        candidate = str(prefix) if case_sensitive else str(prefix).lower()
        if normalized.startswith(candidate):
            matches.append(str(prefix))

    for marker in validator_contract.get("forbidden_markers") or []:
        candidate = str(marker) if case_sensitive else str(marker).lower()
        if candidate in full_text:
            matches.append(str(marker))

    status = "failed" if matches and validator_contract.get("fail_on_match", True) else "passed"
    template = validator_contract.get("failure_message_template") or "Meta commentary found: {{matches}}."
    unique_matches = sorted(set(matches), key=str.lower)
    return {
        "id": validator_contract.get("id"),
        "status": status,
        "matches": unique_matches,
        "message": template.replace("{{matches}}", ", ".join(unique_matches)) if unique_matches else None,
    }
