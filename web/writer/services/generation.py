from __future__ import annotations

import os

from django.db import transaction

from gaiden.writer_engine.clients import OpenAIEmbeddingClient, QwenGenerator
from gaiden.writer_engine.engine import ChapterRequest, WriterEngine
from gaiden.writer_engine.index import VectorIndex
from ..models import Chapter, ChapterSession


def _engine(chapter: Chapter) -> WriterEngine:
    if not chapter.project.vector_index_path:
        raise ValueError("vectorize the selected project sources before generation")
    embedder = OpenAIEmbeddingClient(
        base_url=os.environ.get("GAIDEN_EMBEDDING_BASE_URL", "http://127.0.0.1:8001/v1"),
        api_key=os.environ.get("GAIDEN_EMBEDDING_API_KEY", "placeholder"),
        model=os.environ.get("GAIDEN_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"),
    )
    generator = QwenGenerator(
        base_url=os.environ.get("GAIDEN_QWEN_BASE_URL", "http://127.0.0.1:8000/v1"),
        api_key=os.environ.get("GAIDEN_QWEN_API_KEY", "placeholder"),
        model=os.environ.get("GAIDEN_QWEN_MODEL", "Qwen/Qwen3.5-9B"),
        thinking=os.environ.get("GAIDEN_QWEN_THINKING", "0").casefold()
        in {"1", "true", "yes", "on"},
    )
    return WriterEngine(
        index=VectorIndex.load(chapter.project.vector_index_path),
        embedder=embedder,
        generator=generator,
    )


def _session_role(number: int, total: int) -> str:
    if total == 1:
        return "Complete the chapter arc, including opening, development, climax and closing."
    position = (number - 1) / max(1, total - 1)
    if position == 0:
        return "Open the chapter, establish the immediate objective and create forward tension."
    if position >= 1:
        return "Deliver the chapter climax, consequences and a compelling but coherent closing beat."
    if position >= 0.66:
        return "Escalate conflict and drive the chapter toward its decisive turn."
    return "Develop action, clues, character pressure and causal progression."


def generate_chapter(chapter: Chapter) -> Chapter:
    if chapter.status == Chapter.Status.FINAL:
        raise ValueError("a finalized chapter cannot be regenerated")
    required = {
        "bíblia do personagem": chapter.project.character_bible,
        "bíblia do antagonista": chapter.project.antagonist_bible,
        "cenários e locais": chapter.project.scenario_bible,
        "mundo, época, clima e referências": chapter.project.world_bible,
        "direção da história": chapter.project.story_direction,
        "roteiro geral": chapter.project.story_outline,
        "direção do capítulo": chapter.direction,
        "roteiro do capítulo": chapter.script,
    }
    missing = [label for label, value in required.items() if not value.strip()]
    if missing:
        raise ValueError("complete before generation: " + ", ".join(missing))
    if chapter.target_words < chapter.session_count * 400:
        raise ValueError("target words must allow at least 400 words per session")
    engine = _engine(chapter)
    chapter.status = Chapter.Status.GENERATING
    chapter.error_message = ""
    chapter.save(update_fields=("status", "error_message", "updated_at"))
    completed = {
        session.number: session
        for session in chapter.sessions.filter(status=ChapterSession.Status.COMPLETE)
    }
    try:
        for number in range(1, chapter.session_count + 1):
            if number in completed:
                continue
            previous = "\n\n".join(
                session.content for session in chapter.sessions.filter(
                    status=ChapterSession.Status.COMPLETE, number__lt=number
                ).order_by("number")
            )
            project = chapter.project
            continuity = (
                f"Character bible:\n{project.character_bible}\n\n"
                f"Antagonist bible:\n{project.antagonist_bible}\n\n"
                f"Scenario and locations:\n{project.scenario_bible}\n\n"
                f"World, period, climate and references:\n{project.world_bible}\n\n"
                f"Story direction:\n{project.story_direction}\n\n"
                f"Story outline:\n{project.story_outline}\n\n"
                f"Previous sessions of this chapter:\n{previous[-12000:]}"
            )
            brief = (
                f"Chapter direction:\n{chapter.direction}\n\n"
                f"Chapter script:\n{chapter.script}\n\n"
                f"This is session {number} of {chapter.session_count}. "
                f"{_session_role(number, chapter.session_count)} "
                "Do not repeat previous sessions and do not close the chapter before the final session."
            )
            result = engine.create_chapter(
                ChapterRequest(
                    title=f"{chapter.title or f'Chapter {chapter.number:02d}'} — session {number}",
                    language=project.language,
                    brief=brief,
                    continuity=continuity,
                    point_of_view="Follow the project and chapter direction exactly",
                    target_words=chapter.words_per_session,
                ),
                top_k=chapter.retrieval_top_k,
            )
            with transaction.atomic():
                ChapterSession.objects.create(
                    chapter=chapter,
                    number=number,
                    status=ChapterSession.Status.COMPLETE,
                    content=result.text,
                    word_count=len(result.text.split()),
                    model=result.model,
                    source_chunk_ids=list(result.source_chunk_ids),
                    source_scores=list(result.source_scores),
                    generation_parameters={
                        "target_words": chapter.words_per_session,
                        "chapter_target_words": chapter.target_words,
                        "session_count": chapter.session_count,
                        "retrieval_top_k": chapter.retrieval_top_k,
                    },
                )
        chapter.status = Chapter.Status.GENERATION_COMPLETE
        chapter.error_message = ""
        chapter.save(update_fields=("status", "error_message", "updated_at"))
        return chapter
    except Exception as exc:
        chapter.status = Chapter.Status.FAILED
        chapter.error_message = str(exc)[:2000]
        chapter.save(update_fields=("status", "error_message", "updated_at"))
        raise
