from __future__ import annotations

from dataclasses import dataclass

from . import edition_meta


@dataclass(frozen=True)
class StagePolicy:
    stages: tuple[str, ...] = ("polish", "refine", "translate")

    def stages_for(self, edition) -> tuple[str, ...]:
        lang = edition_meta.language_code(edition)
        if lang == "de":
            return tuple(stage for stage in self.stages if stage != "polish")
        return self.stages

    def is_stage_enabled(self, edition, stage: str) -> bool:
        return stage in self.stages_for(edition)

    def locked_reference_stage(self, edition) -> str | None:
        if getattr(edition, "lock_polish", False) and self.is_stage_enabled(edition, "polish"):
            return "polish"
        if getattr(edition, "lock_refine", False) and self.is_stage_enabled(edition, "refine"):
            return "refine"
        if getattr(edition, "lock_translate", False) and self.is_stage_enabled(edition, "translate"):
            return "translate"
        return None

    def assert_stage_allowed(self, edition, requested_stage: str) -> None:
        if not self.is_stage_enabled(edition, requested_stage):
            lang = edition_meta.language_code(edition) or "unknown"
            raise PermissionError(f"Stage '{requested_stage}' desativado para idioma '{lang}'.")

        if getattr(edition, "lock_translate", False) and requested_stage in ("refine", "polish"):
            raise PermissionError("Translate esta LOCKED. Refine/Polish nao podem ser executados.")

        if getattr(edition, "lock_refine", False) and requested_stage == "polish":
            raise PermissionError("Refine esta LOCKED. Polish nao pode ser executado.")

        if getattr(edition, "lock_polish", False) and requested_stage in ("translate", "refine", "polish"):
            raise PermissionError(
                "Polish esta LOCKED. Nao reprocessar etapas anteriores sem desbloquear."
            )


POLICY = StagePolicy()
