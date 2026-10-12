from __future__ import annotations

import hashlib
import json


from .book_kit import build_book_kit
from .context_budget import allocate_context
from .source_retrieval import retrieve_source_rag
from ..reference_bible import REFERENCE_BIBLE_PRECEDENCE, effective_reference_bible


def _source_rag(project, chapter) -> list[dict]:
    return retrieve_source_rag(
        project,
        chapter,
        top_k=8,
        max_context_chars=24000,
    )


def build_narrative_contract(chapter, *, persist: bool = True):
    project = chapter.project
    kit = build_book_kit(project)
    ledger = project.continuity_states.filter(through_chapter__lt=chapter.number).order_by("-through_chapter").first()
    summaries = list(project.chapter_summaries.filter(chapter__number__lt=chapter.number).order_by("chapter__number").values(
        "chapter_id", "chapter__number", "short_summary", "major_events", "characters_present",
        "locations", "facts_established", "new_information", "resolved_threads", "open_threads", "continuity_delta"))
    source_rag = _source_rag(project, chapter)
    reference_bible = effective_reference_bible(project)

    payload = {
        "schema_version": "1.0", "project": {"id": project.pk, "title": project.title},
        "book_kit": kit, "chapter": {"number": chapter.number, "title": chapter.title,
        "direction": chapter.direction, "script": chapter.script, "target_words": chapter.target_words},
        "anchor_facts": list(project.anchor_facts or []),
        "continuity": ledger.state_json if ledger else {},
        "history": {"summaries": summaries},
        "references": {
            "reference_bible": reference_bible,
            "source_rag": source_rag,
            "delimited_context": (
                "<REFERENCE_CONTEXT>\n" + json.dumps(source_rag, ensure_ascii=False) + "\n</REFERENCE_CONTEXT>"
                if source_rag else ""
            ),
        },

        # Narrative authority follows the Reference Bible precedence contract;
        # RAG remains reference material only.
        "execution_policy": {
            "primary_authority": [
                "reference_bible",
            ],
            "canonical_support": [
                "continuity_ledger",
                "chapter_summaries",
                "chapter_direction",
                "story_outline",
                "reference_metadata",
            ],
            "reference_only": [
                "source_rag",
            ],
            "rules": [
                "Resolve conflicts in this order: Bible, continuity, direction, outline, references, RAG, model knowledge.",
                "The Reference Bible defines global constraints for characters, world, setting, voice, genre, canon, forbidden elements, project isolation and RAG policy.",
                "Continuity and factual summaries preserve established events and permanent consequences.",
                "Chapter direction and outline control the planned scenes unless they conflict with the Bible or established continuity.",
                "Reference metadata describes allowed source use and must not introduce unselected projects.",
                "Source RAG is literary and contextual reference material only.",
                "Source RAG may influence diction, atmosphere, period texture, dialogue rhythm and descriptive technique.",
                "Source RAG must not change plot, scene order, characters, clues, motivations, chronology, discoveries or outcomes.",
                "If Source RAG conflicts with the Bible, continuity, direction, outline or reference policy, ignore the conflicting RAG material.",
                "Retrieval relevance does not imply narrative authority.",
            ],
        },

        "priority_rules": REFERENCE_BIBLE_PRECEDENCE,
    }
    sections = {"anchor_facts": json.dumps(payload["anchor_facts"]), "chapter_plan": json.dumps(payload["chapter"]),
                "book_kit": json.dumps(kit), "continuity_ledger": json.dumps(payload["continuity"]),
                "history": json.dumps(payload["history"]["summaries"]),
                "reference_bible": reference_bible,
                "source_rag": json.dumps(source_rag, ensure_ascii=False)}
    budget = allocate_context(sections)
    if "source_rag" in budget["dropped_optional_sections"]:
        payload["references"]["source_rag"] = []
        payload["references"]["delimited_context"] = ""
    payload["token_budget"] = budget
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload_sha = hashlib.sha256(canonical.encode()).hexdigest()
    if not persist:
        return payload, payload_sha
    from ..models import NarrativeContract
    existing = NarrativeContract.objects.filter(project=project, chapter=chapter, payload_sha256=payload_sha).first()
    if existing:
        return existing
    version = (NarrativeContract.objects.filter(project=project, chapter=chapter).order_by("-version").values_list("version", flat=True).first() or 0) + 1
    return NarrativeContract.objects.create(project=project, chapter=chapter, version=version,
        book_kit_snapshot=kit, book_kit_sha256=kit["sha256"], chapter_plan=payload["chapter"],
        continuity_snapshot=payload["continuity"], chapter_history=payload["history"],
        source_rag_context=source_rag, narrative_memory_context=[], payload_json=payload,
        payload_sha256=payload_sha, estimated_input_tokens=budget["estimated_input"], token_budget=budget)
