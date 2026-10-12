from __future__ import annotations

import pytest

from gaiden.application.agents.contracts import (
    load_agent_contract,
    resolve_agent,
    validate_polish_contract_for_language,
)
from gaiden.application.agents.stage_resolver import resolve_agent_for_ui_stage


def test_french_polish_aliases_resolve_to_universal_contract():
    aliases = ["fr", "FR", "fr-FR", "french", "français", "francais"]

    for alias in aliases:
        resolved = resolve_agent_for_ui_stage("polish", alias)
        assert resolved["stage"] == "polish"
        assert resolved["language"] == "fr"
        assert resolved["agent_id"] == "polish_fr_universal_2026"
        assert resolved["contract_path"] == "data/contracts/agents/polish_fr_universal_2026.json"


def test_french_polish_contract_has_required_name_model_and_stage():
    contract = load_agent_contract("polish_fr_universal_2026")

    assert contract["contract_name"] == "POLISH_FR"
    assert contract["agent_name"] == "POLISH_FR"
    assert contract["model"] == "gpt-5-chat-latest"
    assert contract["stage"] == "polish"
    assert contract["strictly_linguistic_stage"] is True
    assert contract["structural_cleanup_allowed"] is False


def test_french_polish_stage_resolve_loads_universal_contract():
    contract = resolve_agent("polish", "fr")

    assert contract["contract_name"] == "POLISH_FR"
    assert contract["model"] == "gpt-5-chat-latest"
    assert contract["stage"] == "polish"


def test_legacy_french_polish_contract_is_rejected():
    legacy_contract = {
        "contract_name": "Francês_Polidor",
        "agent_name": "Francês_Polidor",
        "model": "gpt-5-chat-latest",
    }

    with pytest.raises(ValueError):
        validate_polish_contract_for_language("fr", legacy_contract)


def test_french_polish_prompt_contains_essential_rules():
    prompt = load_agent_contract("polish_fr_universal_2026")["system_prompt"]

    required = [
        "Tu es POLISH_FR",
        "Tu ne traduis pas.",
        "Cette étape est strictement linguistique et stylistique.",
        "70 % préservation. 30 % amélioration active.",
        "Jusqu’à 50 % de réécriture locale",
        "préserver exactement la structure reçue",
        "Si le texte est lourd, améliore-le.",
        "Réduis les traces de traduction",
        "Retourne uniquement le texte poli.",
        "Return only the polished French text.",
    ]
    for text in required:
        assert text in prompt
