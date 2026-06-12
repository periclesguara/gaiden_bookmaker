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


def test_modernize_agent_contract_uses_gpt_5_4():
    contract = load_agent_contract("modernize_en_us_2026")

    assert contract["engine"]["default_model"] == "gpt-5.4"
