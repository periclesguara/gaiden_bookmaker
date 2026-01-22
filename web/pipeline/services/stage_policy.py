from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StagePolicy:
    stages: tuple[str, ...] = ("polish", "refine", "translate")

    def locked_reference_stage(self, edition) -> str | None:
        if getattr(edition, "lock_polish", False):
            return "polish"
        if getattr(edition, "lock_refine", False):
            return "refine"
        if getattr(edition, "lock_translate", False):
            return "translate"
        return None

    def assert_stage_allowed(self, edition, requested_stage: str) -> None:
        if getattr(edition, "lock_translate", False) and requested_stage in ("refine", "polish"):
            raise PermissionError("Translate esta LOCKED. Refine/Polish nao podem ser executados.")

        if getattr(edition, "lock_refine", False) and requested_stage == "polish":
            raise PermissionError("Refine esta LOCKED. Polish nao pode ser executado.")

        if getattr(edition, "lock_polish", False) and requested_stage in ("translate", "refine", "polish"):
            raise PermissionError(
                "Polish esta LOCKED. Nao reprocessar etapas anteriores sem desbloquear."
            )


POLICY = StagePolicy()
