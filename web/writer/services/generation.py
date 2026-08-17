from __future__ import annotations

import os

from django.db import transaction

from gaiden.writer_engine.clients import OpenAIEmbeddingClient, QwenGenerator
from gaiden.writer_engine.engine import ChapterRequest, NonfictionRequest, WriterEngine
from gaiden.writer_engine.index import VectorIndex
from writer.language_contract import contract_sha256, validate_language_contract
from writer.models import Chapter, ChapterSession, StoryProject
from writer.services.supporting_characters import (
    cast_snapshot_for_generation,
    supporting_characters_context,
)


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


def _fiction_session_role(number: int, total: int) -> str:
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


def _nonfiction_session_role(number: int, total: int) -> str:
    if total == 1:
        return "Develop the complete argument with a clear opening, analysis and conclusion."
    if number == 1:
        return "Establish the question, thesis and first necessary line of evidence."
    if number == total:
        return "Complete the analysis and close the chapter without introducing an unsupported thesis."
    return "Develop the next distinct part of the argument without repeating earlier sessions."


def _required_fields(chapter: Chapter) -> dict[str, str]:
    if chapter.project.writing_mode == StoryProject.WritingMode.NONFICTION:
        return {
            "direção do capítulo": chapter.direction,
            "texto-base, argumentos e notas": chapter.script,
            "referências e consultas para o RAG": chapter.source_guidance,
        }
    return {
        "bíblia do personagem": chapter.project.character_bible,
        "bíblia do antagonista": chapter.project.antagonist_bible,
        "bíblia dos coadjuvantes": chapter.project.supporting_characters_bible,
        "cenários e locais": chapter.project.scenario_bible,
        "mundo, época, clima e referências": chapter.project.world_bible,
        "direção da história": chapter.project.story_direction,
        "roteiro geral": chapter.project.story_outline,
        "direção do capítulo": chapter.direction,
        "roteiro do capítulo": chapter.script,
    }


def generate_chapter(chapter: Chapter) -> Chapter:
    if chapter.status == Chapter.Status.FINAL:
        raise ValueError("a finalized chapter cannot be regenerated")
    missing = [
        label for label, value in _required_fields(chapter).items() if not value.strip()
    ]
    if missing:
        raise ValueError("complete before generation: " + ", ".join(missing))
    contract = chapter.project.language_contract
    validate_language_contract(contract)
    contract_hash = contract_sha256(contract)
    output_language = contract["target_language"]
    if chapter.target_words < chapter.session_count * 400:
        raise ValueError("target words must allow at least 400 words per session")
    completed = {
        session.number: session
        for session in chapter.sessions.filter(status=ChapterSession.Status.COMPLETE)
    }
    incompatible = [
        session.number
        for session in completed.values()
        if session.language_contract_sha256 != contract_hash
    ]
    if incompatible:
        numbers = ", ".join(str(number) for number in sorted(incompatible))
        raise ValueError(
            "completed sessions use a different or legacy language contract "
            f"(sessions: {numbers}); create a versioned chapter revision"
        )
    mode_incompatible = [
        session.number
        for session in completed.values()
        if session.generation_parameters.get("writing_mode", StoryProject.WritingMode.FICTION)
        != chapter.project.writing_mode
    ]
    if mode_incompatible:
        numbers = ", ".join(str(number) for number in sorted(mode_incompatible))
        raise ValueError(
            f"completed sessions use a different writing mode (sessions: {numbers})"
        )

    cast_snapshot = None
    if chapter.project.writing_mode == StoryProject.WritingMode.FICTION:
        cast_snapshot = cast_snapshot_for_generation(chapter.project)
        incompatible_cast = [
            session.number
            for session in completed.values()
            if session.supporting_cast_sha256 != cast_snapshot.sha256
        ]
        if incompatible_cast:
            numbers = ", ".join(str(number) for number in sorted(incompatible_cast))
            raise ValueError(
                "completed sessions use a different or legacy supporting-cast revision "
                f"(sessions: {numbers}); start a versioned chapter revision"
            )

    engine = _engine(chapter)
    chapter.status = Chapter.Status.GENERATING
    chapter.error_message = ""
    chapter.save(update_fields=("status", "error_message", "updated_at"))
    try:
        for number in range(1, chapter.session_count + 1):
            if number in completed:
                continue
            previous = "\n\n".join(
                session.content
                for session in chapter.sessions.filter(
                    status=ChapterSession.Status.COMPLETE, number__lt=number
                ).order_by("number")
            )
            if chapter.project.writing_mode == StoryProject.WritingMode.NONFICTION:
                result = engine.create_nonfiction_chapter(
                    NonfictionRequest(
                        title=(
                            f"{chapter.title or f'Chapter {chapter.number:02d}'} "
                            f"— session {number}"
                        ),
                        language=output_language,
                        direction=(
                            f"{chapter.direction}\n\nSession {number} of {chapter.session_count}: "
                            f"{_nonfiction_session_role(number, chapter.session_count)}"
                        ),
                        operator_text=chapter.script,
                        source_guidance=chapter.source_guidance,
                        continuity=previous[-12000:],
                        language_contract=contract,
                        target_words=chapter.words_per_session,
                        citation_prefix=f"s{number}",
                    ),
                    top_k=chapter.retrieval_top_k,
                )
            else:
                project = chapter.project
                supporting_context = supporting_characters_context(
                    project.supporting_characters_bible,
                    chapter_number=chapter.number,
                    chapter_count=project.chapter_count,
                )
                continuity = (
                    f"Character bible:\n{project.character_bible}\n\n"
                    f"Antagonist bible:\n{project.antagonist_bible}\n\n"
                    f"{supporting_context}\n\n"
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
                    f"{_fiction_session_role(number, chapter.session_count)} "
                    "Do not repeat previous sessions and do not close the chapter before the final session."
                )
                result = engine.create_chapter(
                    ChapterRequest(
                        title=(
                            f"{chapter.title or f'Chapter {chapter.number:02d}'} "
                            f"— session {number}"
                        ),
                        language=output_language,
                        brief=brief,
                        continuity=continuity,
                        point_of_view="Follow the project and chapter direction exactly",
                        language_contract=contract,
                        target_words=chapter.words_per_session,
                    ),
                    top_k=chapter.retrieval_top_k,
                )

            content = result.text
            parameters = {
                "writing_mode": chapter.project.writing_mode,
                "target_words": chapter.words_per_session,
                "chapter_target_words": chapter.target_words,
                "session_count": chapter.session_count,
                "retrieval_top_k": chapter.retrieval_top_k,
                "validation_attempts": result.attempts,
            }
            if chapter.project.writing_mode == StoryProject.WritingMode.NONFICTION:
                parameters["citation_contract"] = "rag-chunk-footnotes-v1"
            with transaction.atomic():
                ChapterSession.objects.create(
                    chapter=chapter,
                    number=number,
                    status=ChapterSession.Status.COMPLETE,
                    content=content,
                    word_count=len(content.split()),
                    model=result.model,
                    source_chunk_ids=list(result.source_chunk_ids),
                    source_scores=list(result.source_scores),
                    generation_parameters=parameters,
                    language_contract=contract,
                    language_contract_sha256=contract_hash,
                    supporting_cast_revision=(cast_snapshot.revision if cast_snapshot else None),
                    supporting_cast_snapshot=(cast_snapshot.registry if cast_snapshot else {}),
                    supporting_cast_sha256=(cast_snapshot.sha256 if cast_snapshot else ""),
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
