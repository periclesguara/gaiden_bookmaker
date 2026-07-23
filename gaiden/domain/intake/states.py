from __future__ import annotations

from enum import Enum


class IntakeState(str, Enum):
    DISCOVERED = "DISCOVERED"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOADED = "DOWNLOADED"
    CLEANING = "CLEANING"
    CLEAN_READY = "CLEAN_READY"
    READY_FOR_CODEX = "READY_FOR_CODEX"
    TRANSLATING = "TRANSLATING"
    TRANSLATION_RETURNED = "TRANSLATION_RETURNED"
    READY_FOR_EDITING = "READY_FOR_EDITING"
    FAILED = "FAILED"


class InvalidIntakeTransition(ValueError):
    pass


ALLOWED_TRANSITIONS: dict[IntakeState, frozenset[IntakeState]] = {
    IntakeState.DISCOVERED: frozenset({IntakeState.DOWNLOADING, IntakeState.DOWNLOADED, IntakeState.FAILED}),
    IntakeState.DOWNLOADING: frozenset({IntakeState.DOWNLOADED, IntakeState.FAILED}),
    IntakeState.DOWNLOADED: frozenset({IntakeState.CLEANING, IntakeState.FAILED}),
    IntakeState.CLEANING: frozenset({IntakeState.CLEAN_READY, IntakeState.FAILED}),
    IntakeState.CLEAN_READY: frozenset({IntakeState.READY_FOR_CODEX, IntakeState.FAILED}),
    IntakeState.READY_FOR_CODEX: frozenset(
        {IntakeState.TRANSLATING, IntakeState.TRANSLATION_RETURNED, IntakeState.FAILED}
    ),
    IntakeState.TRANSLATING: frozenset({IntakeState.TRANSLATION_RETURNED, IntakeState.FAILED}),
    IntakeState.TRANSLATION_RETURNED: frozenset({IntakeState.READY_FOR_EDITING, IntakeState.FAILED}),
    IntakeState.READY_FOR_EDITING: frozenset(),
    IntakeState.FAILED: frozenset(),
}


def transition_state(current: str | IntakeState, target: str | IntakeState) -> IntakeState:
    try:
        current_state = IntakeState(current)
        target_state = IntakeState(target)
    except ValueError as exc:
        raise InvalidIntakeTransition(f"Unknown intake state: {exc}") from exc
    if target_state not in ALLOWED_TRANSITIONS[current_state]:
        raise InvalidIntakeTransition(f"Invalid intake transition: {current_state} -> {target_state}")
    return target_state
