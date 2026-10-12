from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from gaiden.writer_engine.clients import LocalEmbeddingClient
from gaiden.writer_engine.engine import ChapterRequest, WriterEngine
from gaiden.writer_engine.index import VectorIndex
from gaiden.writer_engine.local_generator import writer_generator

from ..models import Chapter
from ..reference_bible import effective_reference_bible
from .validation import contract_instructions, scene_contract
from .contracts import build_narrative_contract
from .execution_order import chapter_is_sequentially_eligible


def _engine(chapter: Chapter) -> WriterEngine:
    if not chapter.project.vector_index_path:
        raise ValueError("vectorize the selected project sources before generation")
    return WriterEngine(
        index=VectorIndex.load(Path(chapter.project.vector_index_path)),
        embedder=LocalEmbeddingClient(
            model=os.environ.get("GAIDEN_LOCAL_EMBEDDING_MODEL", ""),
        ),
        generator=writer_generator(),
    )


def _previous_context(chapter: Chapter) -> str:
    summaries = chapter.project.chapter_summaries.filter(chapter__number__lt=chapter.number).order_by("chapter__number").values(
        "chapter__number", "short_summary", "major_events", "facts_established", "open_threads", "continuity_delta")
    return json.dumps(list(summaries), ensure_ascii=False)


def build_chapter_request(chapter: Chapter) -> tuple[ChapterRequest, object]:
    project = chapter.project
    contract = scene_contract(chapter, 1, [])
    continuity = (
        f"Primary writing language: {project.effective_primary_language}\n"
        f"Character bible:\n{project.character_bible}\n\n"
        f"Supporting characters bible:\n{project.supporting_characters_bible}\n\n"
        f"Antagonist bible:\n{project.antagonist_bible}\n\n"
        f"Scenario and locations:\n{project.scenario_bible}\n\n"
        f"World, period, climate and references:\n{project.world_bible}\n\n"
        f"Previous chapter continuity:\n{_previous_context(chapter)}"
    )
    language_rule = project.language_rules.filter(language_code=project.effective_primary_language).first()
    if language_rule and language_rule.rules:
        continuity = f"Active language rules:\n{language_rule.rules}\n\n{continuity}"
    references = project.references.filter(enabled=True).select_related("intake_work")
    if references:
        reference_lines = [f"- {ref.intake_work.title if ref.intake_work else ref.source.edition.work.title}: {', '.join(ref.roles or ['OTHER'])}" for ref in references]
        continuity = "Canonical reference works (metadata only; retrieve relevant passages separately):\n" + "\n".join(reference_lines) + "\n\n" + continuity
    continuity = f"Project reference bible (global book context):\n{effective_reference_bible(project)}\n\n{continuity}"
    brief = (
        f"Chapter direction:\n{chapter.direction}\n\n"
        f"Complete chapter script, including any internal parts or scenes:\n{chapter.script}\n\n"
        "Write the complete chapter in one response. Internal parts are narrative instructions, "
        "not separate generation units. Do not mention this contract or the generation process."
    )
    return ChapterRequest(
        title=chapter.title or f"Chapter {chapter.number:02d}",
        language=project.language,
        brief=brief + contract_instructions(contract),
        continuity=continuity,
        point_of_view=contract.pov,
        target_words=chapter.target_words,
    ), contract


def generate_chapter(chapter: Chapter, **_legacy_kwargs) -> Chapter:
    eligible, reason = chapter_is_sequentially_eligible(chapter)
    if not eligible:
        raise ValueError(reason)
    with transaction.atomic():
        current = Chapter.objects.select_for_update().get(pk=chapter.pk)
        if current.generated_text.strip() or current.final_text.strip():
            raise ValueError("generated chapter already exists; use an explicit revision workflow")
        if current.status == Chapter.Status.GENERATING:
            raise ValueError("Este capítulo já está em geração; não dispare novamente.")
        current.status = Chapter.Status.GENERATING
        current.error_message = ""
        current.save(update_fields=("status", "error_message", "updated_at"))
    try:
        contract_record = build_narrative_contract(chapter)
        request, contract = build_chapter_request(chapter)
        result = _engine(chapter).create_chapter(request, top_k=chapter.retrieval_top_k)
        chapter.refresh_from_db()
        chapter.generated_text = result.text
        chapter.generation_model = result.model
        chapter.generation_source_chunk_ids = list(result.source_chunk_ids)
        chapter.generation_source_scores = list(result.source_scores)
        chapter.generation_contract_sha256 = contract_record.payload_sha256
        chapter.generation_parameters = {
            "target_words": chapter.target_words,
            "retrieval_top_k": chapter.retrieval_top_k,
            "contract": contract.__dict__,
            "narrative_contract_id": contract_record.pk,
        }
        chapter.generation_metadata = {"chapter_summary": "", "state_delta": {}}
        chapter.generated_at = timezone.now()
        chapter.status = Chapter.Status.GENERATION_COMPLETE
        chapter.error_message = ""
        chapter.save(update_fields=(
            "generated_text", "generation_model", "generation_source_chunk_ids",
            "generation_source_scores", "generation_contract_sha256",
            "generation_parameters", "generation_metadata", "generated_at",
            "status", "error_message", "updated_at",
        ))
        return chapter
    except Exception as exc:
        chapter.refresh_from_db()
        chapter.status = Chapter.Status.FAILED
        chapter.error_message = str(exc)[:2000]
        chapter.save(update_fields=("status", "error_message", "updated_at"))
        raise
