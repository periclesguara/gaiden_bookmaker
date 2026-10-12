from __future__ import annotations

import json
from typing import Any


def _json_summary(label: str, payload: Any) -> str:
    return f"{label}:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def _validator_terms(validator_contracts: list[dict[str, Any]]) -> list[str]:
    terms: list[str] = []
    for contract in validator_contracts:
        for key in ("terms", "phrase_terms", "forbidden_prefixes", "forbidden_markers"):
            values = contract.get(key)
            if isinstance(values, list):
                terms.extend(str(value) for value in values)
    return terms


def build_messages(
    agent_contract: dict[str, Any],
    language_contract: dict[str, Any],
    stage_contract: dict[str, Any],
    validator_contracts: list[dict[str, Any]],
    source_text: str,
) -> list[dict[str, str]]:
    prompt_template = agent_contract.get("prompt_template") or {}
    system_prompt = prompt_template.get("system")
    developer_rules = prompt_template.get("developer")
    user_template = prompt_template.get("user_template")

    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise ValueError("Agent contract prompt_template.system is required.")
    if not isinstance(developer_rules, list):
        raise ValueError("Agent contract prompt_template.developer must be a list.")
    if not isinstance(user_template, str) or "{{source_text}}" not in user_template:
        raise ValueError("Agent contract prompt_template.user_template must contain {{source_text}}.")

    developer_sections = [
        "\n".join(str(rule) for rule in developer_rules),
        _json_summary("LANGUAGE CONTRACT", language_contract),
        _json_summary("STAGE OUTPUT REQUIREMENTS", stage_contract.get("output_requirements", {})),
        _json_summary("VALIDATOR CONTRACTS", validator_contracts),
    ]
    forbidden_terms = _validator_terms(validator_contracts)
    if forbidden_terms:
        developer_sections.append("FORBIDDEN TERMS AND MARKERS:\n" + "\n".join(f"- {term}" for term in forbidden_terms))

    return [
        {"role": "system", "content": system_prompt},
        {"role": "developer", "content": "\n\n".join(developer_sections)},
        {"role": "user", "content": user_template.replace("{{source_text}}", source_text)},
    ]
