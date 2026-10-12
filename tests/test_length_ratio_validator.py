from __future__ import annotations

from gaiden.application.agents.contracts import load_json_contract
from gaiden.application.agents.validators.length_ratio_validator import validate_length_ratio


def test_length_ratio_returns_manual_review_when_output_too_short():
    contract = load_json_contract("data/contracts/validators/length_ratio_basic.json")
    source = "This is a longer source text. " * 20

    result = validate_length_ratio(source, "Too short.", contract)

    assert result["status"] == "manual_review"
