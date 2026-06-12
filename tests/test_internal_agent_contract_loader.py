from __future__ import annotations

from gaiden.application.agents.contracts import load_agent_contract, load_json_contract, resolve_agent


def test_registry_resolves_modernize_en_us_agent():
    contract = resolve_agent("modernize", "en_us")

    assert contract["id"] == "modernize_en_us_2026"


def test_agent_contract_loads_gpt_5_4_default_model():
    contract = load_agent_contract("modernize_en_us_2026")

    assert contract["engine"]["default_model"] == "gpt-5.4"


def test_language_contract_target_year_2026():
    contract = load_json_contract("data/contracts/languages/en_us_2026.json")

    assert contract["target_year"] == 2026
