from __future__ import annotations

import os


class ContextBudgetExceeded(ValueError):
    pass


def _limit(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def allocate_context(sections: dict[str, str], *, reserved_output: int | None = None) -> dict:
    total = _limit("GAIDEN_WRITER_MAX_CONTEXT_TOKENS", 24000)
    reserved = reserved_output or _limit("GAIDEN_WRITER_RESERVED_OUTPUT_TOKENS", 8000)
    mandatory = ["anchor_facts", "chapter_plan", "book_kit", "continuity_ledger", "history", "reference_bible"]
    estimate = lambda value: max(1, len(value) // 4)
    mandatory_tokens = sum(estimate(sections.get(key, "")) for key in mandatory)
    if mandatory_tokens > total:
        raise ContextBudgetExceeded({"limit": total, "reserved_output": reserved,
                                     "estimated_input": mandatory_tokens,
                                     "included_sections": mandatory,
                                     "dropped_optional_sections": []})
    result = {key: sections[key] for key in mandatory if sections.get(key)}
    used = mandatory_tokens
    dropped = []
    for key in ("source_rag",):
        value = sections.get(key, "")
        if value and used + estimate(value) <= total:
            result[key] = value
            used += estimate(value)
        elif value:
            dropped.append(key)
    return {"sections": result, "limit": total, "reserved_output": reserved,
            "estimated_input": used, "dropped_optional_sections": dropped}
