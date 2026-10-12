from __future__ import annotations

from typing import Any

from gaiden.application.agents.heading_normalization import ROMAN_TO_INT, STRUCTURAL_HEADING_RE


def validate_no_roman_heading_numerals(
    source_text: str,
    output_text: str,
    validator_contract: dict[str, Any],
) -> dict[str, Any]:
    del source_text
    matches: list[dict[str, Any]] = []
    for line_no, line in enumerate(output_text.splitlines(), 1):
        match = STRUCTURAL_HEADING_RE.match(line)
        if not match:
            continue
        roman = match.group("roman").upper()
        if roman not in ROMAN_TO_INT:
            continue
        matches.append(
            {
                "line": line_no,
                "heading": line.strip(),
                "label": match.group("label").upper(),
                "roman": roman,
                "expected": f"{match.group('label')} {ROMAN_TO_INT[roman]}",
            }
        )

    status = "failed" if matches and validator_contract.get("fail_on_match", True) else "passed"
    return {
        "id": validator_contract.get("id"),
        "status": status,
        "matches": matches,
        "message": validator_contract.get("failure_message") if matches else None,
    }
