from __future__ import annotations

import pytest

from gaiden.application.agents.contracts import (
    load_agent_contract,
    resolve_agent,
    validate_refine_contract_for_language,
)
from gaiden.application.agents.stage_resolver import resolve_agent_for_ui_stage


def test_french_refine_aliases_resolve_to_universal_contract():
    aliases = ["fr", "FR", "fr-FR", "french", "français", "francais"]

    for alias in aliases:
        resolved = resolve_agent_for_ui_stage("refine", alias)
        assert resolved["stage"] == "refine"
        assert resolved["language"] == "fr"
        assert resolved["agent_id"] == "fr_refine_universal_2026"
        assert resolved["contract_path"] == "data/contracts/agents/fr_refine_universal_2026.json"


def test_french_refine_contract_has_required_name_model_and_mode():
    contract = load_agent_contract("fr_refine_universal_2026")

    assert contract["contract_name"] == "FR_REFINE_UNIVERSAL"
    assert contract["agent_name"] == "FR_REFINE_UNIVERSAL"
    assert contract["model"] == "gpt-5.5"
    assert contract["mode"] == "refine_existing_french"


def test_french_refine_stage_resolve_loads_universal_contract():
    contract = resolve_agent("refine", "fr")

    assert contract["contract_name"] == "FR_REFINE_UNIVERSAL"
    assert contract["model"] == "gpt-5.5"
    assert contract["mode"] == "refine_existing_french"


def test_legacy_french_refine_contract_is_rejected():
    legacy_contract = {
        "contract_name": "LE_GRAN_COULHON",
        "agent_name": "LE_GRAN_COULHON_TRANSLATE",
        "model": "gpt-5-chat-latest",
    }

    with pytest.raises(ValueError):
        validate_refine_contract_for_language("fr", legacy_contract)


def test_french_refine_prompt_contains_essential_rules():
    prompt = load_agent_contract("fr_refine_universal_2026")["system_prompt"]

    required = [
        "Your task is not to translate from scratch.",
        "Keep approximately 70% of the text close to the translated base.",
        "Allow up to 30% supervised rewriting",
        "Keep total length variation within approximately ±15%",
        "reduce excessive sentence length",
        "reduce remaining archaisms",
        "improve rhythm",
        "improve cadence",
        "improve musicality",
        "Return only the refined French text.",
        "Never merge separate numbered aphorisms",
    ]
    for text in required:
        assert text in prompt


def test_existing_en_us_refine_route_is_unchanged():
    resolved = resolve_agent_for_ui_stage("refine", "en_us")

    assert resolved["stage"] == "refine"
    assert resolved["language"] == "en_us"
    assert resolved["agent_id"] == "refine_en_us_2026"
