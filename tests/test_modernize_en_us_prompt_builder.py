from __future__ import annotations

from gaiden.application.agents.contracts import load_agent_contract, load_json_contract
from gaiden.application.agents.prompt_builder import build_messages


def test_prompt_builder_includes_contract_rules_terms_and_source_text():
    agent_contract = load_agent_contract("modernize_en_us_2026")
    refs = agent_contract["contract_refs"]
    language_contract = load_json_contract(refs["language"])
    stage_contract = load_json_contract(refs["stage"])
    validator_contracts = [load_json_contract(path) for path in refs["validators"]]

    messages = build_messages(
        agent_contract,
        language_contract,
        stage_contract,
        validator_contracts,
        "Thou hast done this.",
    )

    assert messages[0]["role"] == "system"
    assert "Modernize EN-US 2026 agent" in messages[0]["content"]
    assert messages[1]["role"] == "developer"
    assert "Follow the JSON contract strictly." in messages[1]["content"]
    assert "thou" in messages[1]["content"]
    assert "preserve_headings" in messages[1]["content"]
    assert messages[2]["role"] == "user"
    assert "Thou hast done this." in messages[2]["content"]
