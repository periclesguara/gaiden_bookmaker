from __future__ import annotations

from gaiden.application.agents.contracts import load_agent_contract, load_json_contract
from gaiden.application.agents.prompt_builder import build_messages


def test_refine_prompt_builder_includes_contract_rules_and_source_text():
    agent_contract = load_agent_contract("refine_en_us_2026")
    refs = agent_contract["contract_refs"]
    language_contract = load_json_contract(refs["language"])
    stage_contract = load_json_contract(refs["stage"])
    validator_contracts = [load_json_contract(path) for path in refs["validators"]]

    source_text = "The room was quiet, and he looked at the door."
    messages = build_messages(
        agent_contract,
        language_contract,
        stage_contract,
        validator_contracts,
        source_text,
    )

    assert messages[0]["role"] == "system"
    assert "Refine EN-US 2026 agent" in messages[0]["content"]
    assert "70%" in messages[0]["content"]
    assert "30%" in messages[0]["content"]
    assert messages[1]["role"] == "developer"
    assert "fluency" in messages[1]["content"]
    assert "elegance" in messages[1]["content"]
    assert "cadence" in messages[1]["content"]
    assert "musicality" in messages[1]["content"]
    assert "Reduce overly long sentences" in messages[1]["content"]
    assert "contractions sparingly" in messages[1]["content"]
    assert "remaining archaic language" in messages[1]["content"]
    assert "Preserve headings" in messages[1]["content"]
    assert "preserve_headings" in messages[1]["content"]
    assert messages[2]["role"] == "user"
    assert "preserve meaning" in messages[0]["content"].lower()
    assert "70% semantic fidelity" in messages[2]["content"]
    assert "30% assisted rewriting" in messages[2]["content"]
    assert source_text in messages[2]["content"]


def test_refine_agent_contract_declares_controlled_rewrite_ratio():
    agent_contract = load_agent_contract("refine_en_us_2026")

    assert agent_contract["refine_policy"]["semantic_preservation_target"] == 0.70
    assert agent_contract["refine_policy"]["assisted_rewrite_allowance"] == 0.30
    assert agent_contract["refine_policy"]["ratio_is_editorial_guidance_not_strict_metric"] is True
    assert agent_contract["contractions_policy"]["use_contractions_sparingly"] is True
