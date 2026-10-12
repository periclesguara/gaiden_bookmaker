from __future__ import annotations

from gaiden.application.agents.contracts import load_json_contract
from gaiden.application.agents.validators.no_meta_commentary_validator import validate_no_meta_commentary


def test_no_meta_commentary_fails_with_forbidden_prefix():
    contract = load_json_contract("data/contracts/validators/no_meta_commentary.json")

    result = validate_no_meta_commentary("", "Here is the modernized text:", contract)

    assert result["status"] == "failed"
    assert "here is" in result["matches"]
