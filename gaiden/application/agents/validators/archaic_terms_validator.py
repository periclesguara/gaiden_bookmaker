from __future__ import annotations

import re
from typing import Any


def _flags(contract: dict[str, Any]) -> int:
    return 0 if contract.get("case_sensitive") else re.IGNORECASE


def _word_pattern(value: str, match_mode: str) -> re.Pattern[str]:
    escaped = re.escape(value)
    if match_mode == "word_boundary":
        return re.compile(rf"\b{escaped}\b", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


def validate_archaic_terms(source_text: str, output_text: str, validator_contract: dict[str, Any]) -> dict[str, Any]:
    del source_text
    flags = _flags(validator_contract)
    match_mode = str(validator_contract.get("match_mode") or "word_boundary")
    matches: list[str] = []

    for phrase in validator_contract.get("phrase_terms") or []:
        pattern = re.compile(re.escape(str(phrase)), flags)
        if pattern.search(output_text):
            matches.append(str(phrase))

    for term in validator_contract.get("terms") or []:
        pattern = re.compile(
            rf"\b{re.escape(str(term))}\b" if match_mode == "word_boundary" else re.escape(str(term)),
            flags,
        )
        if pattern.search(output_text):
            matches.append(str(term))

    unique_matches = sorted(set(matches), key=str.lower)
    status = "failed" if unique_matches and validator_contract.get("fail_on_match", True) else "passed"
    template = validator_contract.get("failure_message_template") or "Forbidden archaic terms found: {{matches}}."
    return {
        "id": validator_contract.get("id"),
        "status": status,
        "matches": unique_matches,
        "message": template.replace("{{matches}}", ", ".join(unique_matches)) if unique_matches else None,
    }
