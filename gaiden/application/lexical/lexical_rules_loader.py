from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from gaiden.infrastructure import paths

logger = logging.getLogger(__name__)

STAGE_RULE_FILES = {
    "translate": "translate_hard_replace.json",
    "refine": "refine_soft_replace.json",
    "polish": "polish_contextual_replace.json",
    "qa": "qa_modernization_check.json",
}

def normalize_stage(stage: str | None) -> str:
    value = (stage or "").strip().lower()
    aliases = {
        "agent_translate": "translate",
        "agent_translate_default": "translate",
        "translation": "translate",
        "aldebaran_refine": "refine",
        "aldebaran_refine_return": "refine",
        "return_aldebaran": "refine",
        "polish_agent": "polish",
        "polidor": "polish",
        "qa_modernization": "qa",
    }
    return aliases.get(value, value)


def rules_path_for_stage(stage: str | None, language: str | None = "en") -> Path | None:
    normalized_stage = normalize_stage(stage)
    filename = STAGE_RULE_FILES.get(normalized_stage)
    if not filename:
        return None
    lang = (language or "en").strip().lower().replace("-", "_")
    if not lang.startswith("en"):
        return None
    return paths.get_data_root() / "lexical_rules" / "global" / "en" / filename


def load_stage_rules(stage: str | None, language: str | None = "en") -> dict[str, Any]:
    path = rules_path_for_stage(stage, language)
    if path is None:
        return {}
    if not path.is_file():
        logger.warning("Lexical rules file missing for stage=%s language=%s path=%s", stage, language, path)
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load lexical rules for stage=%s language=%s path=%s: %s", stage, language, path, exc)
        return {}
    if not isinstance(payload, dict):
        logger.warning("Invalid lexical rules payload for stage=%s language=%s path=%s: expected object", stage, language, path)
        return {}
    return payload
