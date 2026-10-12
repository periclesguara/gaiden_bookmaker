from __future__ import annotations

from typing import Any

from .archaic_terms_validator import validate_archaic_terms
from .length_ratio_validator import validate_length_ratio
from .no_meta_commentary_validator import validate_no_meta_commentary
from .no_roman_heading_numerals import validate_no_roman_heading_numerals


def run_validators(
    source_text: str,
    output_text: str,
    validator_contracts: list[dict[str, Any]],
) -> dict[str, Any]:
    validators = []
    for contract in validator_contracts:
        validator_type = contract.get("type")
        if validator_type == "forbidden_terms_validator":
            result = validate_archaic_terms(source_text, output_text, contract)
        elif validator_type == "forbidden_prefix_and_marker_validator":
            result = validate_no_meta_commentary(source_text, output_text, contract)
        elif validator_type == "length_ratio_validator":
            result = validate_length_ratio(source_text, output_text, contract)
        elif validator_type == "heading_roman_numerals_validator":
            result = validate_no_roman_heading_numerals(source_text, output_text, contract)
        else:
            raise ValueError(f"Unknown validator type: {validator_type}")
        validators.append(result)

    if any(item["status"] == "failed" for item in validators):
        status = "failed"
    elif any(item["status"] == "manual_review" for item in validators):
        status = "manual_review"
    else:
        status = "passed"
    return {"status": status, "validators": validators}
