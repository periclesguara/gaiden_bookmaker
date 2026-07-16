from __future__ import annotations

from typing import Protocol

from gaiden.domain.intake import IntakeState, transition_state


class StatefulIntakeItem(Protocol):
    status: str
    last_error: str

    def save(self, *args, **kwargs) -> None:
        ...


def transition_item(item: StatefulIntakeItem, target: str | IntakeState, *, error: str = "") -> IntakeState:
    next_state = transition_state(item.status, target)
    item.status = next_state.value
    item.last_error = error if next_state is IntakeState.FAILED else ""
    item.save(update_fields=["status", "last_error", "updated_at"])
    return next_state
