from __future__ import annotations

import logging
from pathlib import Path

from gaiden.infrastructure import paths

from .lexical_rules_loader import normalize_stage

logger = logging.getLogger(__name__)


def normalize_language(language: str | None) -> str:
    value = (language or "en").strip().lower().replace("-", "_")
    if value.startswith("en"):
        return "en"
    return value


def stage_contract_path(stage: str | None, language: str | None = "en") -> Path:
    normalized_stage = normalize_stage(stage)
    normalized_language = normalize_language(language)
    return (
        paths.get_data_root()
        / "lexical_rules"
        / "global"
        / normalized_language
        / "contracts"
        / f"{normalized_stage}_contract_{normalized_language}.txt"
    )


def load_stage_contract(stage: str | None, language: str | None = "en") -> str:
    normalized_stage = normalize_stage(stage)
    normalized_language = normalize_language(language)
    path = stage_contract_path(normalized_stage, normalized_language)
    if not path.is_file():
        logger.warning(
            "Stage contract not found for stage=%s, language=%s. Continuing without contract.",
            normalized_stage,
            normalized_language,
        )
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.warning(
            "Failed to load stage contract for stage=%s, language=%s path=%s: %s",
            normalized_stage,
            normalized_language,
            path,
            exc,
        )
        return ""
