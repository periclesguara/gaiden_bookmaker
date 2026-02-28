from __future__ import annotations

from typing import Any, Dict

from gaiden.translate_artifacts import normalize_mode


VALID_STAGE_MODES = {"do", "skip"}


def normalize_stage_mode(mode: str | None, *, default: str = "do") -> str:
    raw = str(mode or default).strip().lower()
    if raw in VALID_STAGE_MODES:
        return raw
    return default


def apply_skip_policy(
    *,
    selected_mode: str | None,
    split_mode: str | None,
    refine_mode: str | None,
) -> Dict[str, Any]:
    selected = normalize_mode(selected_mode, default="automatic")
    original_split = normalize_stage_mode(split_mode, default="do")
    original_refine = normalize_stage_mode(refine_mode, default="do")
    skip_requested = original_split == "skip" or original_refine == "skip"

    corrected = False
    final_split = original_split
    final_refine = original_refine
    skip_block_reason = None

    if selected == "automatic" and skip_requested:
        corrected = True
        final_split = "do"
        final_refine = "do"
        skip_block_reason = "automatic_mode"

    skip_applied = final_split == "skip" or final_refine == "skip"
    result: Dict[str, Any] = {
        "selected_mode": selected,
        "effective_mode": selected,
        "split_mode": final_split,
        "refine_mode": final_refine,
        "skip_requested": skip_requested,
        "skip_applied": skip_applied,
        "skip_block_reason": skip_block_reason,
        "skip_corrected": corrected,
    }
    if corrected:
        result["skip_original_split_mode"] = original_split
        result["skip_original_refine_mode"] = original_refine
    return result
