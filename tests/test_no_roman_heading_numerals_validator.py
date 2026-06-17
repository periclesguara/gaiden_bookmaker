from __future__ import annotations

from gaiden.application.agents.contracts import load_json_contract
from gaiden.application.agents.validators.no_roman_heading_numerals import validate_no_roman_heading_numerals


def _contract():
    return load_json_contract("data/contracts/validators/no_roman_heading_numerals.json")


def test_validator_fails_for_roman_heading():
    result = validate_no_roman_heading_numerals("", "BOOK IX\n\nText.", _contract())

    assert result["status"] == "failed"
    assert result["matches"][0]["heading"] == "BOOK IX"


def test_validator_passes_for_arabic_heading():
    result = validate_no_roman_heading_numerals("", "BOOK 9\n\nText.", _contract())

    assert result["status"] == "passed"


def test_validator_ignores_roman_numerals_in_prose():
    result = validate_no_roman_heading_numerals("", "This mentions World War II in prose.", _contract())

    assert result["status"] == "passed"
