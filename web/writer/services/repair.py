"""Operator-authorized repair of existing Writer sessions, coordinated by Django."""
from __future__ import annotations

import hashlib

from django.db import transaction
from django.utils import timezone

from ..models import Chapter, ChapterSession
from .generation import (
    _engine,
    build_session_request,
    validated_session_parameters,
)
from .scenes import validate_session_scenes
from .validation import generate_validated_session


def repair_chapter_sessions(chapter: Chapter, session_numbers) -> Chapter:
    numbers = tuple(sorted(set(session_numbers)))
    if not numbers or any(number < 1 or number > chapter.session_count for number in numbers):
        raise ValueError("Informe sessões válidas deste capítulo.")

    with transaction.atomic():
        current = Chapter.objects.select_for_update().get(pk=chapter.pk)
        if current.status == Chapter.Status.FINAL:
            raise ValueError("Um capítulo finalizado não pode ser reparado.")
        if current.status == Chapter.Status.GENERATING:
            raise ValueError("Este capítulo já está em geração ou reparo.")
        existing = {
            session.number: session
            for session in current.sessions.filter(number__in=numbers)
        }
        if set(existing) != set(numbers) or any(
            session.status != ChapterSession.Status.COMPLETE
            for session in existing.values()
        ):
            raise ValueError("Todas as sessões selecionadas devem existir e estar concluídas.")
        current.status = Chapter.Status.GENERATING
        current.error_message = ""
        current.save(update_fields=("status", "error_message", "updated_at"))

    chapter.refresh_from_db()
    try:
        completed_numbers = chapter.sessions.filter(
            status=ChapterSession.Status.COMPLETE
        ).values_list("number", flat=True)
        scenes = validate_session_scenes(
            chapter.session_scenes, chapter.session_count, completed_numbers
        ) if chapter.session_scenes else []
        engine = _engine(chapter)
        for number in numbers:
            original = chapter.sessions.get(number=number)
            original_sha256 = hashlib.sha256(original.content.encode()).hexdigest()
            previous, contract, request = build_session_request(chapter, number, scenes)
            validated = generate_validated_session(
                chapter,
                number,
                engine,
                request,
                contract,
                previous,
                original_text=original.content,
            )
            result = validated.result
            parameters = validated_session_parameters(
                chapter, number, scenes, result, validated
            )
            parameters.update({
                "repair": True,
                "repaired_from_sha256": original_sha256,
            })
            with transaction.atomic():
                locked = ChapterSession.objects.select_for_update().get(pk=original.pk)
                current_sha256 = hashlib.sha256(locked.content.encode()).hexdigest()
                if current_sha256 != original_sha256:
                    raise RuntimeError(
                        f"A sessão {number} mudou durante o reparo; o texto novo não foi salvo."
                    )
                locked.content = result.text
                locked.word_count = len(result.text.split())
                locked.model = result.model
                locked.source_chunk_ids = list(result.source_chunk_ids)
                locked.source_scores = list(result.source_scores)
                locked.generation_parameters = parameters
                locked.error_message = ""
                locked.save(update_fields=(
                    "content", "word_count", "model", "source_chunk_ids",
                    "source_scores", "generation_parameters", "error_message",
                ))
            chapter.refresh_from_db()
        chapter.status = Chapter.Status.GENERATION_COMPLETE
        chapter.error_message = ""
        chapter.save(update_fields=("status", "error_message", "updated_at"))
        return chapter
    except Exception as exc:
        chapter.status = Chapter.Status.FAILED
        chapter.error_message = str(exc)[:2000]
        chapter.save(update_fields=("status", "error_message", "updated_at"))
        raise
