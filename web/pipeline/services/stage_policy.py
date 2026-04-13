from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StagePolicy:
    stages: tuple[str, ...] = ("polish", "refine", "translate")

    def locked_reference_stage(self, edition) -> str | None:
        return None

    def assert_stage_allowed(self, edition, requested_stage: str) -> None:
        return None


POLICY = StagePolicy()
