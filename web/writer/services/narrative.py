"""Django orchestration for one scene merge using the configured local model."""
from __future__ import annotations

import hashlib
from django.db import transaction
from django.utils import timezone
from gaiden.writer_engine.narrative import merge_scenes
from gaiden.writer_engine.local_generator import writer_generator
from ..models import Chapter, ChapterSession
from .generation import generate_chapter


def _scene_digest(sessions: list[ChapterSession]) -> str:
    payload = "\n\n".join(f"{session.number}\0{session.content}" for session in sessions)
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
    chapter = _claim_merge(chapter)
    if chapter.status == Chapter.Status.FINAL:
        return chapter
    sessions = list(chapter.sessions.filter(status=ChapterSession.Status.COMPLETE).order_by("number"))
    digest = _scene_digest(sessions)
    try:
        if chapter.merged_text:
            if chapter.merge_input_sha256 != digest:
                raise ValueError("as cenas não correspondem ao merge preservado")
        else:
            merger = writer_generator()
            chapter.merged_text = merge_scenes(
                merger, language=chapter.project.language, chapter_number=chapter.number,
                chapter_title=chapter.title or f"Chapter {chapter.number:02d}",
                scene_texts=[session.content for session in sessions], target_words=chapter.target_words,
            )
            chapter.merge_model = merger.model
            chapter.merge_input_sha256 = digest
            chapter.merged_at = timezone.now()
            chapter.save(update_fields=("merged_text", "merge_model", "merge_input_sha256", "merged_at", "updated_at"))
        chapter.status = Chapter.Status.MERGED
        chapter.error_message = ""
        chapter.save(update_fields=("status", "error_message", "updated_at"))
        return chapter
    except Exception as exc:
        chapter.status = Chapter.Status.FAILED
        chapter.error_message = str(exc)[:2000]
        chapter.save(update_fields=("status", "error_message", "updated_at"))
        raise


def run_chapter_workflow(chapter: Chapter) -> Chapter:
    """Generate one complete chapter request; finalization remains explicit."""
    chapter.refresh_from_db()
    if chapter.status == Chapter.Status.FINAL or chapter.generated_text:
        return chapter
    return generate_chapter(chapter)
