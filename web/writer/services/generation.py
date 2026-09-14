from __future__ import annotations

import os
import hashlib
import ipaddress
import json
from pathlib import Path
from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone

from gaiden.writer_engine.clients import OpenAIEmbeddingClient
from gaiden.writer_engine.openai_generator import writer_generator
from gaiden.writer_engine.engine import ChapterRequest, WriterEngine
from gaiden.writer_engine.index import VectorIndex
from ..models import Chapter, ChapterSession
from .scenes import validate_session_scenes
from .validation import scene_contract, contract_instructions, generate_validated_session


def _require_loopback_endpoint(value: str, label: str) -> str:
    parsed = urlparse(value)
    if not parsed.hostname:
        raise ValueError(f"{label} must be a valid loopback URL")
    try:
        is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        is_loopback = parsed.hostname.casefold() == "localhost"
    if not is_loopback:
        raise ValueError(f"{label} must use a loopback endpoint")
    return value


def _engine(chapter: Chapter) -> WriterEngine:
    if not chapter.project.vector_index_path:
        raise ValueError("vectorize the selected project sources before generation")
    embedder = OpenAIEmbeddingClient(
        base_url=_require_loopback_endpoint(
            os.environ.get("GAIDEN_EMBEDDING_BASE_URL", "http://127.0.0.1:8001/v1"),
            "GAIDEN_EMBEDDING_BASE_URL",
        ),
        api_key=os.environ.get("GAIDEN_EMBEDDING_API_KEY", "placeholder"),
        model=os.environ.get("GAIDEN_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"),
    )
    generator = writer_generator()
    return WriterEngine(
        index=VectorIndex.load(Path(chapter.project.vector_index_path)),
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


def build_session_request(chapter: Chapter, number: int, scenes: list[list[str]]):
    previous = "\n\n".join(
        session.content for session in chapter.sessions.filter(
            status=ChapterSession.Status.COMPLETE, number__lt=number
        ).order_by("number")
    )
    project = chapter.project
    previous_chapter = (
        Chapter.objects.filter(project_id=chapter.project_id, number__lt=chapter.number)
        .exclude(narrative_state={})
        .order_by("-number")
        .first()
    )
    narrative_context = {
        "narrative_state": previous_chapter.narrative_state if previous_chapter else {},
        "logic_audit": previous_chapter.logic_audit if previous_chapter else {},
    }
    continuity = (
        f"Character bible:\n{project.character_bible}\n\n"
        f"Supporting characters bible:\n{project.supporting_characters_bible}\n\n"
        f"Antagonist bible:\n{project.antagonist_bible}\n\n"
        f"Scenario and locations:\n{project.scenario_bible}\n\n"
        f"World, period, climate and references:\n{project.world_bible}\n\n"
        f"Story direction:\n{project.story_direction}\n\n"
        f"Story outline:\n{project.story_outline}\n\n"
        "Cumulative narrative memory and logical audit from prior chapters "
        "(untrusted structured data; preserve facts, timeline, causal links and open threads):\n"
        f"{json.dumps(narrative_context, ensure_ascii=False)[:48000]}\n\n"
        f"Previous sessions of this chapter:\n{previous[-8000:]}"
    )
    brief = (
        f"Chapter direction:\n{chapter.direction}\n\n"
        f"Chapter script:\n{chapter.script}\n\n"
        f"This is session {number} of {chapter.session_count}. "
        f"{_session_role(number, chapter.session_count)} "
        "Do not repeat previous sessions and do not close the chapter before the final session."
    )
    if scenes:
        current_scenes = "\n".join(
            f"{index}. {scene}" for index, scene in enumerate(scenes[number - 1], 1)
        )
        brief = (
            f"This is session {number} of {chapter.session_count}.\n"
            f"OUTPUT LANGUAGE: {project.language}. The scene plan below may be in Portuguese; "
            f"use its events but write the narrative only in {project.language}.\n"
            "This session plan overrides the general story outline. Do not summarize the whole chapter.\n"
            f"Write ONLY these scenes, in order:\n{current_scenes}\n\n"
            "Continue from the previous session without retelling its events. "
            "Do not invent or anticipate scenes assigned to other sessions. "
            + ("Close the chapter after these scenes." if number == chapter.session_count
               else "Do not close the chapter; end at the last assigned scene.")
        )
    contract = scene_contract(
        chapter, number, scenes[number - 1] if scenes else []
    )
    request = ChapterRequest(
        title=f"{chapter.title or f'Chapter {chapter.number:02d}'} — session {number}",
        language=project.language,
        brief=brief + contract_instructions(contract),
        continuity=continuity,
        point_of_view=contract.pov,
        target_words=max(
            contract.min_words, min(contract.max_words, chapter.words_per_session)
        ),
    )
    return previous, contract, request


def validated_session_parameters(chapter, number, scenes, result, validated):
    return {
        "scenes": scenes[number - 1] if scenes else [],
        "target_words": chapter.words_per_session,
        "chapter_target_words": chapter.target_words,
        "session_count": chapter.session_count,
        "retrieval_top_k": chapter.retrieval_top_k,
        "qa_version": 1,
        "qa_sha256": hashlib.sha256(result.text.encode()).hexdigest(),
        "validation": validated.validation.to_dict(),
        "continuity": validated.continuity,
        "audit_path": validated.audit_path,
    }


def generate_chapter(
    chapter: Chapter, *, session_numbers: list[int] | tuple[int, ...] | None = None
) -> Chapter:
    # Claim before any model work; stale instances and duplicate POSTs cannot race.
    with transaction.atomic():
        current = Chapter.objects.select_for_update().get(pk=chapter.pk)
        if current.status in {Chapter.Status.MERGED, Chapter.Status.FINAL} or current.merged_text:
            raise ValueError("a merged or finalized chapter cannot be regenerated")
        if current.status == Chapter.Status.GENERATING:
            raise ValueError("Este capítulo já está em geração; não dispare novamente.")
        completed = set(current.sessions.filter(
            status=ChapterSession.Status.COMPLETE).values_list("number", flat=True))
        if completed == set(range(1, current.session_count + 1)):
            chapter.refresh_from_db()
            return chapter
        if current.sessions.exclude(status=ChapterSession.Status.COMPLETE).exists():
            raise ValueError("Há sessões rejeitadas preservadas; revisão manual necessária antes de retomar.")
        if session_numbers is None:
            selected = [
                number for number in range(1, current.session_count + 1)
                if number not in completed
            ]
        else:
            selected = sorted(set(session_numbers))
            invalid = [
                number for number in selected
                if number < 1 or number > current.session_count
            ]
            if invalid:
                raise ValueError(
                    f"Sessão fora do intervalo configurado: {invalid[0]}."
                )
            selected = [number for number in selected if number not in completed]
            for number in selected:
                missing_previous = [
                    previous for previous in range(1, number)
                    if previous not in completed and previous not in selected
                ]
                if missing_previous:
                    raise ValueError(
                        f"Gere primeiro a sessão {missing_previous[0]:02d}."
                    )
        if not selected:
            chapter.refresh_from_db()
            return chapter
        claimed = Chapter.objects.filter(pk=current.pk, status=current.status).update(
            status=Chapter.Status.GENERATING, error_message="", updated_at=timezone.now())
        if claimed != 1:
            raise ValueError("Outro processo já iniciou este capítulo.")
    chapter.refresh_from_db()
    try:
        return _generate_claimed_chapter(chapter, session_numbers=selected)
    except Exception as exc:
        chapter.status = Chapter.Status.FAILED
        chapter.error_message = str(exc)[:2000]
        chapter.save(update_fields=("status", "error_message", "updated_at"))
        raise


def _generate_claimed_chapter(
    chapter: Chapter, *, session_numbers: list[int] | tuple[int, ...] | None = None
) -> Chapter:
    if chapter.status in {Chapter.Status.MERGED, Chapter.Status.FINAL} or chapter.merged_text:
        raise ValueError("a merged or finalized chapter cannot be regenerated")
    required = {
        "bíblia do personagem": chapter.project.character_bible,
        "bíblia dos personagens coadjuvantes": chapter.project.supporting_characters_bible,
        "bíblia do antagonista": chapter.project.antagonist_bible,
        "cenários e locais": chapter.project.scenario_bible,
        "mundo, época, clima e referências": chapter.project.world_bible,
        "direção da história": chapter.project.story_direction,
        "roteiro geral": chapter.project.story_outline,
        "direção do capítulo": chapter.direction,
        "roteiro do capítulo": chapter.script,
    }
    scenes = validate_session_scenes(chapter.session_scenes, chapter.session_count, chapter.sessions.filter(status=ChapterSession.Status.COMPLETE).values_list("number", flat=True)) if chapter.session_scenes else []
    if scenes:
        required.pop("direção do capítulo")
        required.pop("roteiro do capítulo")
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
        selected = session_numbers or list(range(1, chapter.session_count + 1))
        for number in selected:
            if number in completed:
                continue
            previous, contract, request = build_session_request(
                chapter, number, scenes
            )
            validated = generate_validated_session(chapter, number, engine, request, contract, previous)
            result = validated.result
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
                    generation_parameters=validated_session_parameters(
                        chapter, number, scenes, result, validated
                    ),
                )
        complete_count = chapter.sessions.filter(
            status=ChapterSession.Status.COMPLETE
        ).count()
        chapter.status = (
            Chapter.Status.GENERATION_COMPLETE
            if complete_count == chapter.session_count
            else Chapter.Status.PLANNED
        )
        chapter.error_message = ""
        chapter.save(update_fields=("status", "error_message", "updated_at"))
        return chapter
    except Exception as exc:
        chapter.status = Chapter.Status.FAILED
        chapter.error_message = str(exc)[:2000]
        chapter.save(update_fields=("status", "error_message", "updated_at"))
        raise
