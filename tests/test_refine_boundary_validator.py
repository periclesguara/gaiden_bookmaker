from __future__ import annotations

from gaiden.application.pipeline.boundary_validator import (
    auto_repair_boundaries,
    validate_boundaries,
)


def test_boundary_validator_detects_cease_having_children_break():
    report = validate_boundaries(
        "Then their bodies are in their prime, and they will also cease\n\n"
        "Having children at the right time.\n"
    )

    assert not report["ok"]
    assert report["errors"][0]["type"] == "INCOMPLETE_SENTENCE_BOUNDARY_ERROR"


def test_boundary_validator_detects_for_just_as_duplication():
    report = validate_boundaries(
        "For just as\n\n"
        "Just as their minds are, in a sense, distorted from their natural condition.\n"
    )

    assert not report["ok"]
    assert report["errors"][0]["type"] == "BOUNDARY_DUPLICATION_ERROR"


def test_auto_repair_fixes_mechanical_boundary_breaks():
    repaired, repairs = auto_repair_boundaries(
        "first founded so that we might live, but continuing so that we may\n\n"
        "To live well.\n"
    )

    assert repairs
    assert "continuing so that we may to live well" not in repaired
    assert "continuing so that we may live well." in repaired
    assert validate_boundaries(repaired)["ok"]


def test_auto_repair_removes_clear_connector_duplication():
    repaired, repairs = auto_repair_boundaries(
        "For just as\n\n"
        "Just as their minds are distorted.\n"
    )

    assert repairs
    assert repaired == "For just as their minds are distorted.\n"
    assert validate_boundaries(repaired)["ok"]


def test_auto_repair_does_not_change_ambiguous_boundary():
    text = "The constitution is difficult\n\nThe legislator must decide.\n"

    repaired, repairs = auto_repair_boundaries(text)

    assert repaired == text
    assert repairs == []
    assert validate_boundaries(text)["ok"]
