from __future__ import annotations

from gaiden.application.agents.contracts import load_agent_contract
from gaiden.application.agents.stage_resolver import resolve_agent_for_ui_stage


def test_translate_en_us_resolves_to_modernize_agent():
    resolved = resolve_agent_for_ui_stage("translate", "en_us")

    assert resolved["stage"] == "modernize"
    assert resolved["language"] == "en_us"
    assert resolved["agent_id"] == "modernize_en_us_2026"
    assert resolved["contract_path"] == "data/contracts/agents/modernize_en_us_2026.agent.json"


def test_translate_en_us_aliases_normalize_to_en_us():
    assert resolve_agent_for_ui_stage("translate", "en-US")["language"] == "en_us"
    assert resolve_agent_for_ui_stage("translate", "English US")["language"] == "en_us"
    assert resolve_agent_for_ui_stage("translate", "English United States")["language"] == "en_us"
    assert resolve_agent_for_ui_stage("translate", "US English")["language"] == "en_us"


def test_refine_en_us_resolves_to_refine_agent():
    resolved = resolve_agent_for_ui_stage("refine", "en_us")

    assert resolved["stage"] == "refine"
    assert resolved["language"] == "en_us"
    assert resolved["agent_id"] == "refine_en_us_2026"
    assert resolved["contract_path"] == "data/contracts/agents/refine_en_us_2026.agent.json"


def test_refine_fr_resolves_to_universal_refine_agent():
    resolved = resolve_agent_for_ui_stage("refine", "fr")

    assert resolved["stage"] == "refine"
    assert resolved["language"] == "fr"
    assert resolved["agent_id"] == "fr_refine_universal_2026"
    assert resolved["contract_path"] == "data/contracts/agents/fr_refine_universal_2026.json"


def test_refine_en_us_aliases_normalize_to_en_us():
    assert resolve_agent_for_ui_stage("refine", "en-US")["language"] == "en_us"
    assert resolve_agent_for_ui_stage("refine", "English US")["language"] == "en_us"
    assert resolve_agent_for_ui_stage("refine", "English United States")["language"] == "en_us"
    assert resolve_agent_for_ui_stage("refine", "US English")["language"] == "en_us"


def test_modernize_agent_contract_uses_gpt_5_4():
    contract = load_agent_contract("modernize_en_us_2026")

    assert contract["engine"]["default_model"] == "gpt-5.4"


def test_refine_agent_contract_uses_gpt_5_4():
    contract = load_agent_contract("refine_en_us_2026")

    assert contract["engine"]["default_model"] == "gpt-5.4"
