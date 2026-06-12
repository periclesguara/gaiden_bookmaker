from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json_contract(path: str | Path) -> dict[str, Any]:
    contract_path = Path(path)
    if not contract_path.exists():
        raise FileNotFoundError(f"Contract file not found: {contract_path}")
    try:
        data = json.loads(contract_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON contract: {contract_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"JSON contract must be an object: {contract_path}")
    return data


def _enabled_agents(registry: dict[str, Any]) -> list[dict[str, Any]]:
    agents = registry.get("agents")
    if not isinstance(agents, list):
        raise ValueError("Agent registry must contain an agents list.")
    return [agent for agent in agents if isinstance(agent, dict) and agent.get("enabled", True)]


def load_agent_contract(
    agent_id: str,
    registry_path: str | Path = "data/contracts/agent_registry.json",
) -> dict[str, Any]:
    registry = load_json_contract(registry_path)
    for agent in _enabled_agents(registry):
        if agent.get("id") == agent_id:
            contract_path = agent.get("contract_path")
            if not contract_path:
                raise ValueError(f"Agent {agent_id} has no contract_path.")
            return load_json_contract(contract_path)
    raise LookupError(f"Enabled agent not found: {agent_id}")


def resolve_agent(
    stage: str,
    language: str,
    registry_path: str | Path = "data/contracts/agent_registry.json",
) -> dict[str, Any]:
    registry = load_json_contract(registry_path)
    rules = registry.get("resolution_rules")
    if not isinstance(rules, list):
        raise ValueError("Agent registry must contain a resolution_rules list.")

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        when = rule.get("when") or {}
        if when.get("stage") == stage and when.get("language") == language:
            agent_id = rule.get("use_agent")
            if not agent_id:
                raise ValueError(f"Resolution rule for {stage}/{language} has no use_agent.")
            return load_agent_contract(str(agent_id), registry_path=registry_path)

    for agent in _enabled_agents(registry):
        if agent.get("stage") == stage and agent.get("language") == language and agent.get("default"):
            contract_path = agent.get("contract_path")
            if not contract_path:
                raise ValueError(f"Default agent for {stage}/{language} has no contract_path.")
            return load_json_contract(contract_path)

    raise LookupError(f"No agent resolved for stage={stage} language={language}")
