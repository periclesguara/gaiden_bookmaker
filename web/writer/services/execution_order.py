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


def chapter_is_sequentially_eligible(chapter) -> tuple[bool, str]:
    if not chapter.direction.strip() or not chapter.script.strip():
        return False, "chapter direction and complete script are required"
    previous = chapter.project.chapters.filter(number__lt=chapter.number).order_by("-number").first()
    if previous and not hasattr(previous, "narrative_summary"):
        return False, "previous chapter must be closed before this chapter"
    return True, "ready"
