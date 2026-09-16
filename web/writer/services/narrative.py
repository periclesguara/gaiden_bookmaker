"""Django orchestration for the chapter merge and cumulative narrative state."""
from __future__ import annotations

import hashlib

from django.db import transaction
from django.utils import timezone

from gaiden.writer_engine.narrative import (
    audit_narrative_logic,
    merge_scenes,
    update_narrative_state,
)
from gaiden.writer_engine.openai_generator import writer_generator

from ..models import Chapter, ChapterSession
from .generation import generate_chapter


def narrative_memory_generator():
    return writer_generator()


def logic_audit_generator():
    """Use the Writer model for logic review."""
    return writer_generator()


def previous_chapter_context(chapter: Chapter) -> tuple[dict, dict]:
    previous = (
        Chapter.objects.filter(project_id=chapter.project_id, number__lt=chapter.number)
        .exclude(narrative_state={})
        .order_by("-number")
        .first()
    )
    if previous is None:
        return {}, {}
    return previous.narrative_state, previous.logic_audit


def _scene_digest(sessions: list[ChapterSession]) -> str:
    payload = "\n\n".join(
        f"{session.number}\0{session.content}" for session in sessions
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _claim_merge(chapter: Chapter) -> Chapter:
    with transaction.atomic():
        current = Chapter.objects.select_for_update().get(pk=chapter.pk)
        if current.status == Chapter.Status.FINAL:
            return current
        if current.status in {Chapter.Status.GENERATING, Chapter.Status.MERGING}:
            raise ValueError("Este capítulo já está em geração ou merge; não dispare novamente.")
        if not current.qa_ready:
            raise ValueError("todas as cenas/sessões configuradas precisam ser validadas antes do merge")
        current.status = Chapter.Status.MERGING
        current.error_message = ""
        current.save(update_fields=("status", "error_message", "updated_at"))
        return current


def complete_chapter_workflow(chapter: Chapter) -> Chapter:
    """Resume merge, memory, and audit without overwriting any completed stage."""
    chapter = _claim_merge(chapter)
    if chapter.status == Chapter.Status.FINAL:
        return chapter
    sessions = list(chapter.sessions.filter(
        status=ChapterSession.Status.COMPLETE
    ).order_by("number"))
    digest = _scene_digest(sessions)
    previous_state, _previous_audit = previous_chapter_context(chapter)
    try:
        if chapter.merged_text:
            if chapter.merge_input_sha256 != digest:
                raise ValueError("as cenas não correspondem ao merge preservado")
        else:
            merger = writer_generator()
            merged = merge_scenes(
                merger,
                language=chapter.project.language,
                chapter_number=chapter.number,
                chapter_title=chapter.title or f"Chapter {chapter.number:02d}",
                scene_texts=[session.content for session in sessions],
                target_words=chapter.target_words,
            )
            chapter.merged_text = merged
            chapter.merge_model = merger.model
            chapter.merge_input_sha256 = digest
            chapter.merged_at = timezone.now()
            chapter.save(update_fields=(
                "merged_text", "merge_model", "merge_input_sha256", "merged_at", "updated_at",
            ))

        if not chapter.narrative_state:
            memory = update_narrative_state(
                narrative_memory_generator(),
                chapter_number=chapter.number,
                previous_state=previous_state,
                merged_chapter=chapter.merged_text,
            )
            chapter.narrative_state = memory.value
            chapter.narrative_memory_model = memory.model
            chapter.save(update_fields=(
                "narrative_state", "narrative_memory_model", "updated_at",
            ))

        if not chapter.logic_audit:
            audit = audit_narrative_logic(
                logic_audit_generator(),
                chapter_number=chapter.number,
                previous_state=previous_state,
                current_state=chapter.narrative_state,
                merged_chapter=chapter.merged_text,
                creative_constraints={
                    "character_bible": chapter.project.character_bible,
                    "supporting_characters_bible": chapter.project.supporting_characters_bible,
                    "antagonist_bible": chapter.project.antagonist_bible,
                    "story_direction": chapter.project.story_direction,
                    "story_outline": chapter.project.story_outline,
                },
            )
            chapter.logic_audit = audit.value
            chapter.logic_audit_model = audit.model
            chapter.save(update_fields=(
                "logic_audit", "logic_audit_model", "updated_at",
            ))

        chapter.status = Chapter.Status.MERGED
        chapter.memory_updated_at = timezone.now()
        chapter.error_message = ""
        chapter.save(update_fields=(
            "status", "memory_updated_at", "error_message", "updated_at",
        ))
        return chapter
    except Exception as exc:
        chapter.status = Chapter.Status.FAILED
        chapter.error_message = str(exc)[:2000]
        chapter.save(update_fields=("status", "error_message", "updated_at"))
        raise


def run_chapter_workflow(chapter: Chapter) -> Chapter:
    """Generate one complete chapter request; finalization remains explicit."""
    from .generation import generate_chapter

    chapter.refresh_from_db()
    if chapter.status == Chapter.Status.FINAL or chapter.generated_text:
        return chapter
    return generate_chapter(chapter)
