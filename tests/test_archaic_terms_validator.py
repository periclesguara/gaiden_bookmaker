from __future__ import annotations

from gaiden.application.agents.contracts import load_json_contract
from gaiden.application.agents.validators.archaic_terms_validator import validate_archaic_terms


def _contract():
    return load_json_contract("data/contracts/validators/archaic_terms.en_us_2026.json")


def test_archaic_validator_fails_with_archaic_terms():
    result = validate_archaic_terms("", "Thou hast done this.", _contract())

    assert result["status"] == "failed"
    assert "thou" in result["matches"]


def test_archaic_validator_passes_modern_text():
    result = validate_archaic_terms("", "You have done this.", _contract())

    assert result["status"] == "passed"


def test_archaic_validator_does_not_fail_common_art_noun():
    result = validate_archaic_terms("", "The art of war is ancient.", _contract())

    assert result["status"] == "passed"
