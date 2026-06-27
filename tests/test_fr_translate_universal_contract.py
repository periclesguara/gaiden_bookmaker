from __future__ import annotations

import pytest

from gaiden.application.agents.contracts import (
    load_agent_contract,
    resolve_agent,
    validate_translate_contract_for_language,
)
from gaiden.application.agents.stage_resolver import resolve_agent_for_ui_stage
from gaiden.tools.agent_translate_default import resolve_agent_for_target


def test_french_translate_aliases_resolve_to_universal_contract():
    aliases = ["fr", "FR", "fr-FR", "french", "français", "francais"]

    for alias in aliases:
        resolved = resolve_agent_for_ui_stage("translate", alias)
        assert resolved["stage"] == "translate"
        assert resolved["language"] == "fr"
        assert resolved["agent_id"] == "fr_translate_universal_2026"
        assert resolved["contract_path"] == "data/contracts/agents/fr_translate_universal_2026.json"


def test_french_translate_contract_has_required_name_and_model():
    contract = load_agent_contract("fr_translate_universal_2026")

    assert contract["contract_name"] == "FR_TRANSLATE_UNIVERSAL"
    assert contract["agent_name"] == "FR_TRANSLATE_UNIVERSAL"
    assert contract["model"] == "gpt-5.5"
    assert contract["target_language"] == "fr"


def test_french_translate_stage_resolve_loads_universal_contract():
    contract = resolve_agent("translate", "fr")

    assert contract["contract_name"] == "FR_TRANSLATE_UNIVERSAL"
    assert contract["model"] == "gpt-5.5"


def test_legacy_french_translate_contract_is_rejected():
    legacy_contract = {
        "contract_name": "LE_GRAN_COULHON",
        "agent_name": "LE_GRAN_COULHON_TRANSLATE",
        "model": "gpt-5.4",
    }

    with pytest.raises(ValueError):
        validate_translate_contract_for_language("fr", legacy_contract)


def test_french_translate_prompt_contains_essential_rules():
    prompt = load_agent_contract("fr_translate_universal_2026")["system_prompt"]

    required = [
        "If the input is in English, translate it into polished modern French.",
        "If the input is already in French, revise it directly in French.",
        "If the input is mixed English and French",
        "Return only the final French text.",
        "BOOK I → LIVRE 1",
        "remove internal pipeline language if it appears accidentally",
    ]
    for text in required:
        assert text in prompt


def test_agent_translate_default_for_french_ignores_legacy_requested_agent():
    assert resolve_agent_for_target(suffix="fr") == "fr_translate_universal_2026"
    assert (
        resolve_agent_for_target(suffix="fr", requested_agent="LE_GRAN_COULHON")
        == "fr_translate_universal_2026"
    )


def test_existing_en_us_translate_route_is_unchanged():
    resolved = resolve_agent_for_ui_stage("translate", "en_us")

    assert resolved["stage"] == "modernize"
    assert resolved["language"] == "en_us"
    assert resolved["agent_id"] == "modernize_en_us_2026"
