from __future__ import annotations

from pathlib import Path
from typing import Any

from gaiden.application.agents.contracts import contract_path_for_agent, load_json_contract

EN_US_ALIASES = {
    "en",
    "en_us",
    "en-us",
    "en us",
    "en_us",
    "english",
    "english us",
    "english-us",
    "english_us",
    "english united states",
    "english-united-states",
    "english_united_states",
    "us english",
    "us-english",
    "us_english",
}


def normalize_target_language_alias(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "en_us"
    lowered = raw.lower().replace("_", " ").replace("-", " ")
    compact = " ".join(lowered.split())
    if compact in {alias.replace("_", " ").replace("-", " ") for alias in EN_US_ALIASES}:
        return "en_us"
    return raw.strip().lower().replace("-", "_").replace(" ", "_")


def resolve_agent_for_ui_stage(
    ui_stage: str,
    target_language: str,
    registry_path: str | Path = "data/contracts/agent_registry.json",
) -> dict[str, Any]:
    registry = load_json_contract(registry_path)
    normalized_stage = (ui_stage or "").strip().lower()
    normalized_language = normalize_target_language_alias(target_language)

    rules = registry.get("resolution_rules")
    if not isinstance(rules, list):
        raise ValueError("Agent registry must contain a resolution_rules list.")

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        when = rule.get("when") or {}
        when_stage = (when.get("ui_stage") or "").strip().lower()
        when_language = normalize_target_language_alias(str(when.get("target_language") or ""))
        if when_stage == normalized_stage and when_language == normalized_language:
            resolved = rule.get("resolve_to")
            if not isinstance(resolved, dict):
                raise ValueError(f"UI resolution rule for {ui_stage}/{target_language} has no resolve_to.")
            agent_id = str(resolved["agent_id"])
            return {
                "stage": str(resolved["stage"]),
                "language": normalize_target_language_alias(str(resolved["language"])),
                "agent_id": agent_id,
                "contract_path": contract_path_for_agent(agent_id, registry_path=registry_path),
            }

    if normalized_stage == "translate" and normalized_language == "en_us":
        agent_id = "modernize_en_us_2026"
        return {
            "stage": "modernize",
            "language": "en_us",
            "agent_id": agent_id,
            "contract_path": contract_path_for_agent(agent_id, registry_path=registry_path),
        }

    raise LookupError(f"No UI agent resolved for ui_stage={ui_stage} target_language={target_language}")


def is_translate_en_us(ui_stage: str, target_language: str | None) -> bool:
    return (ui_stage or "").strip().lower() == "translate" and normalize_target_language_alias(target_language) == "en_us"
