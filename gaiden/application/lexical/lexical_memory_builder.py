from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from gaiden.infrastructure import paths


def _iter_rule_terms(stage_rules: dict[str, Any]) -> list[str]:
    rules = stage_rules.get("rules") if isinstance(stage_rules, dict) else None
    if not isinstance(rules, dict):
        return []
    return [str(term) for term in rules.keys() if str(term).strip()]


def _term_occurrences(text: str, term: str) -> list[dict[str, Any]]:
    pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
    occurrences: list[dict[str, Any]] = []
    for match in pattern.finditer(text or ""):
        start = match.start()
        line = (text or "")[:start].count("\n") + 1
        occurrences.append(
            {
                "term": match.group(0),
                "line": line,
                "start": start,
                "context": (text or "")[max(0, start - 60) : match.end() + 60].replace("\n", " "),
            }
        )
    return occurrences


def lexical_memory_path(book_code: str, language: str, chunk_id: str) -> Path:
    safe_chunk_id = (chunk_id or "chunk").replace("/", "_")
    return paths.get_data_root() / "translated" / book_code / language / "lexical_memory" / f"{safe_chunk_id}.lexical.json"


def build_lexical_memory(
    *,
    book_code: str,
    language: str,
    chunk_id: str,
    stage: str,
    text: str,
    stage_rules: dict[str, Any],
) -> dict[str, Any]:
    detected: dict[str, Any] = {}
    for term in _iter_rule_terms(stage_rules):
        occurrences = _term_occurrences(text, term)
        if occurrences:
            detected[term] = {
                "count": len(occurrences),
                "occurrences": occurrences[:20],
            }

    payload: dict[str, Any] = {
        "book_code": book_code,
        "language": language,
        "stage": stage,
        "chunk_id": chunk_id,
        "rule_type": stage_rules.get("rule_type") if isinstance(stage_rules, dict) else None,
        "detected_terms": detected,
        "detected_count": sum(item["count"] for item in detected.values()),
        "whole_word_scan": True,
    }
    path = lexical_memory_path(book_code, language, chunk_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["path"] = str(path)
    return payload
