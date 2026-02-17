from __future__ import annotations

from typing import Any

from gaiden.translate_artifacts import normalize_mode
from gaiden.translate_mode_policy import apply_skip_policy


def state_selected_mode(run_state: Any, fallback: str = "automatic") -> str:
    selected = str(getattr(run_state, "selected_mode", "") or "").strip()
    if selected:
        return normalize_mode(selected, default=fallback)
    effective = str(getattr(run_state, "effective_mode", "") or "").strip()
    if effective:
        return normalize_mode(effective, default=fallback)
    return normalize_mode(fallback, default="automatic")


def resolve_policy_from_state(
    run_state: Any,
    *,
    selected_mode: str | None = None,
    split_mode: str | None = None,
    refine_mode: str | None = None,
    fallback_selected_mode: str = "automatic",
) -> dict:
    effective_selected_mode = (
        selected_mode
        if selected_mode is not None
        else state_selected_mode(run_state, fallback=fallback_selected_mode)
    )
    effective_split_mode = (
        split_mode
        if split_mode is not None
        else str(getattr(run_state, "split_mode", "") or "do")
    )
    effective_refine_mode = (
        refine_mode
        if refine_mode is not None
        else str(getattr(run_state, "refine_mode", "") or "do")
    )
    return apply_skip_policy(
        selected_mode=effective_selected_mode,
        split_mode=effective_split_mode,
        refine_mode=effective_refine_mode,
    )


def apply_policy_to_state(run_state: Any, policy: dict) -> None:
    run_state.selected_mode = str(policy.get("selected_mode") or "automatic")
    run_state.effective_mode = str(policy.get("effective_mode") or run_state.selected_mode)
    run_state.split_mode = str(policy.get("split_mode") or "do")
    run_state.refine_mode = str(policy.get("refine_mode") or "do")
