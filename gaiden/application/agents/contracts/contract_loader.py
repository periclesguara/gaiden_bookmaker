from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FORBIDDEN_FRENCH_TRANSLATE_AGENTS = {
    "LE_GRAN_COULHON",
    "LE_GRAN_COULHON_TRANSLATE",
    "LE_GRAND_COULHON",
    "LE_GRAN_COLHOUN",
    "LE_GRAND_COLHOUN",
    "translate_fr_2026",
    "en_fr_litteraire_2026",
    "fr_litteraire",
}

FR_TRANSLATE_AGENT_ID = "fr_translate_universal_2026"
FR_TRANSLATE_CONTRACT_NAME = "FR_TRANSLATE_UNIVERSAL"
FORBIDDEN_FRENCH_REFINE_AGENTS = {
    "LE_GRAN_COULHON",
    "LE_GRAN_COULHON_TRANSLATE",
    "LE_GRAND_COULHON",
    "LE_GRAN_COLHOUN",
    "LE_GRAND_COLHOUN",
    "Le Grand Coulhon",
    "Le_Gran_Colhoun",
    "fr_litteraire",
    "FR_REFINE",
}
FR_REFINE_AGENT_ID = "fr_refine_universal_2026"
FR_REFINE_CONTRACT_NAME = "FR_REFINE_UNIVERSAL"


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


def _normalize_language(value: str | None) -> str:
    raw = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"fr", "fr_fr", "french", "français", "francais"}:
        return "fr"
    if raw in {"en", "en_us", "english", "english_us", "us_english"}:
        return "en_us"
    return raw


def validate_translate_contract_for_language(language: str | None, contract: dict[str, Any]) -> None:
    if _normalize_language(language) != "fr":
        return
    identifiers = {
        str(contract.get("id") or ""),
        str(contract.get("agent_name") or ""),
        str(contract.get("contract_name") or ""),
        str(contract.get("name") or ""),
    }
    if identifiers & FORBIDDEN_FRENCH_TRANSLATE_AGENTS:
        raise ValueError("Forbidden legacy French translate agent resolved.")
    if FR_TRANSLATE_CONTRACT_NAME not in identifiers:
        raise ValueError("French TRANSLATE must resolve to FR_TRANSLATE_UNIVERSAL.")
    if str(contract.get("model") or "").strip() != "gpt-5.5":
        raise ValueError("French TRANSLATE contract must read model gpt-5.5 from fr_translate_universal_2026.json.")


def validate_refine_contract_for_language(language: str | None, contract: dict[str, Any]) -> None:
    identifiers = {
        str(contract.get("id") or ""),
        str(contract.get("agent_name") or ""),
        str(contract.get("contract_name") or ""),
        str(contract.get("name") or ""),
    }
    if identifiers & FORBIDDEN_FRENCH_REFINE_AGENTS:
        raise ValueError("Forbidden legacy French refine agent resolved.")
    if _normalize_language(language) != "fr":
        return
    if FR_REFINE_CONTRACT_NAME not in identifiers:
        raise ValueError("French REFINE must resolve to FR_REFINE_UNIVERSAL.")
    if str(contract.get("mode") or "").strip() != "refine_existing_french":
        raise ValueError("French REFINE contract must use mode refine_existing_french.")
    if str(contract.get("model") or "").strip() != "gpt-5.5":
        raise ValueError("French REFINE contract must read model gpt-5.5 from fr_refine_universal_2026.json.")


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
            contract = load_agent_contract(str(agent_id), registry_path=registry_path)
            if stage == "translate":
                validate_translate_contract_for_language(language, contract)
            if stage == "refine":
                validate_refine_contract_for_language(language, contract)
            return contract

    for agent in _enabled_agents(registry):
        if agent.get("stage") == stage and agent.get("language") == language and agent.get("default"):
            contract_path = agent.get("contract_path")
            if not contract_path:
                raise ValueError(f"Default agent for {stage}/{language} has no contract_path.")
            contract = load_json_contract(contract_path)
            if stage == "translate":
                validate_translate_contract_for_language(language, contract)
            if stage == "refine":
                validate_refine_contract_for_language(language, contract)
            return contract

    raise LookupError(f"No agent resolved for stage={stage} language={language}")


def contract_path_for_agent(
    agent_id: str,
    registry_path: str | Path = "data/contracts/agent_registry.json",
) -> str:
    registry = load_json_contract(registry_path)
    for agent in _enabled_agents(registry):
        if agent.get("id") == agent_id:
            contract_path = agent.get("contract_path")
            if not contract_path:
                raise ValueError(f"Agent {agent_id} has no contract_path.")
            return str(contract_path)
    raise LookupError(f"Enabled agent not found: {agent_id}")
