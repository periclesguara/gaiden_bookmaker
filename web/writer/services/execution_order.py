from __future__ import annotations

from django.utils import timezone

from .contracts import build_narrative_contract


def freeze_contract(chapter):
    """Create the immutable contract handed to the manual external writer."""
    contract = build_narrative_contract(chapter)
    contract.frozen_at = timezone.now()
    contract.status = contract.Status.READY
    contract.save(update_fields=("frozen_at", "status"))
    return contract


def chapter_is_sequentially_eligible(chapter, *, require_plan=True) -> tuple[bool, str]:
    if require_plan and (not chapter.direction.strip() or not chapter.script.strip()):
        return False, "chapter direction and complete script are required"
    for previous in chapter.project.chapters.filter(number__lt=chapter.number).select_related("narrative_summary").order_by("number"):
        if previous.status != previous.Status.FINAL or not previous.final_text.strip():
            return False, f"Concluir o capítulo {previous.number:02d} primeiro."
        summary = getattr(previous, "narrative_summary", None)
        if not summary or not summary.short_summary.strip():
            return False, f"Salvar o resumo de continuidade do capítulo {previous.number:02d}."
    return True, "ready"
