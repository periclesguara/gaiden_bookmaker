from __future__ import annotations

import json
import logging
from typing import Any

from .lexical_memory_builder import build_lexical_memory
from .lexical_rules_loader import load_stage_rules, normalize_stage
from .stage_contract_loader import load_stage_contract, normalize_language

logger = logging.getLogger(__name__)

DEFAULT_AGENT_INSTRUCTION = (
    "Apply only the lexical rules relevant to this stage. Use whole-word matching only; "
    "never replace substrings inside larger words. Preserve meaning, facts, events, "
    "paragraph order, and narrative structure."
)


def build_stage_payload(
    *,
    stage: str,
    book_code: str,
    language: str,
    chunk_id: str,
    text: str,
) -> dict[str, Any]:
    normalized_stage = normalize_stage(stage)
    normalized_language = normalize_language(language)
    stage_rules = load_stage_rules(normalized_stage, normalized_language)
    stage_contract = load_stage_contract(normalized_stage, normalized_language)
    lexical_memory: dict[str, Any] = {}
    if stage_rules:
        lexical_memory = build_lexical_memory(
            book_code=book_code,
            language=normalized_language,
            chunk_id=chunk_id,
            stage=normalized_stage,
            text=text,
            stage_rules=stage_rules,
        )
    payload = {
        "book_code": book_code,
        "language": normalized_language,
        "stage": normalized_stage,
        "chunk_id": chunk_id,
        "stage_contract": stage_contract,
        "stage_rules": stage_rules,
        "lexical_memory": lexical_memory,
        "text": text,
        "agent_instruction": DEFAULT_AGENT_INSTRUCTION,
    }
    logger.info(
        "Lexical stage payload built: stage=%s language=%s book_code=%s chunk_id=%s contract_loaded=%s rules_loaded=%s lexical_memory_generated=%s detected=%s",
        normalized_stage,
        normalized_language,
        book_code,
        chunk_id,
        bool(stage_contract),
        bool(stage_rules),
        bool(lexical_memory),
        lexical_memory.get("detected_count", 0) if lexical_memory else 0,
    )
    return payload


def assemble_stage_user_content(payload: dict[str, Any]) -> str:
    return "\n\n".join(
        [
            "GLOBAL STAGE CONTRACT:\n" + (payload.get("stage_contract") or ""),
            "STAGE RULES:\n" + json.dumps(payload.get("stage_rules") or {}, ensure_ascii=False, indent=2),
            "LEXICAL MEMORY:\n" + json.dumps(payload.get("lexical_memory") or {}, ensure_ascii=False, indent=2),
            "INPUT TEXT:\n" + (payload.get("text") or ""),
            (
                "OUTPUT:\n"
                "Return only the processed text. Do not include notes, markdown, JSON, "
                "explanations, or analysis."
            ),
        ]
    )


def inject_stage_payload(
    *,
    messages: list[dict[str, str]],
    stage: str,
    book_code: str,
    language: str,
    chunk_id: str,
    text: str,
    selected_agent: str | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    payload = build_stage_payload(
        stage=stage,
        book_code=book_code,
        language=language,
        chunk_id=chunk_id,
        text=text,
    )
    if not payload.get("stage_rules") and not payload.get("stage_contract"):
        return messages, payload
    logger.info(
        "Lexical payload injected: book_code=%s language=%s stage=%s chunk_id=%s selected_agent=%s contract_loaded=%s rules_loaded=%s lexical_memory_generated=%s",
        payload.get("book_code"),
        payload.get("language"),
        payload.get("stage"),
        payload.get("chunk_id"),
        selected_agent or "",
        bool(payload.get("stage_contract")),
        bool(payload.get("stage_rules")),
        bool(payload.get("lexical_memory")),
    )
    assembled_content = assemble_stage_user_content(payload)
    updated = list(messages)
    for index in range(len(updated) - 1, -1, -1):
        if updated[index].get("role") == "user":
            updated[index] = {**updated[index], "content": assembled_content}
            return updated, payload
    return [*updated, {"role": "user", "content": assembled_content}], payload
