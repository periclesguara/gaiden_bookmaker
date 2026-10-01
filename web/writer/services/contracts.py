from __future__ import annotations

import hashlib
import json


from .book_kit import build_book_kit
from .context_budget import allocate_context
from .narrative_memory import retrieve_narrative_memory
from .source_retrieval import retrieve_source_rag


def _recent(project, number: int) -> str:
    previous = project.chapters.filter(number__lt=number).order_by("-number").first()
    if not previous:
        return ""
    return (previous.final_text or previous.generated_text)[-12000:]



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
        "chapter_id", "short_summary", "major_events", "open_threads"))
    memory = retrieve_narrative_memory(project, chapter.number, chapter.script)
    source_rag = _source_rag(project, chapter)

    payload = {
        "schema_version": "1.0", "project": {"id": project.pk, "title": project.title},
        "book_kit": kit, "chapter": {"number": chapter.number, "title": chapter.title,
        "direction": chapter.direction, "script": chapter.script, "target_words": chapter.target_words},
        "anchor_facts": list(project.anchor_facts or []),
        "continuity": ledger.state_json if ledger else {},
        "history": {"summaries": summaries, "recent_context": _recent(project, chapter.number),
                    "narrative_memory": memory},
        "references": {
            "reference_bible": project.reference_bible,
            "source_rag": source_rag,
            "delimited_context": (
                "<REFERENCE_CONTEXT>\n" + json.dumps(source_rag, ensure_ascii=False) + "\n</REFERENCE_CONTEXT>"
                if source_rag else ""
            ),
        },

        # Narrative authority is intentionally asymmetric:
        # the current chapter plan controls the story;
        # RAG is reference material only.
        "execution_policy": {
            "primary_authority": [
                "chapter_script",
                "chapter_direction",
            ],
            "canonical_support": [
                "anchor_facts",
                "continuity_ledger",
                "recent_chapter_context",
                "narrative_memory",
            ],
            "secondary_context": [
                "book_kit",
            ],
            "reference_only": [
                "reference_bible",
                "source_rag",
            ],
            "rules": [
                "The current chapter script and direction are the primary narrative authority.",
                "Follow the planned scenes, scene order, dramatic beats, discoveries, character actions and intended ending.",
                "Anchor facts and established continuity are canonical and must not be contradicted.",
                "Recent chapter context and narrative memory preserve continuity but must not replace the current chapter plan.",
                "If an older Book Kit outline conflicts with the current chapter plan or established continuity, ignore the older Book Kit instruction.",
                "Source RAG is literary and contextual reference material only.",
                "Source RAG may influence diction, atmosphere, period texture, dialogue rhythm and descriptive technique.",
                "Source RAG must not change plot, scene order, characters, clues, motivations, chronology, discoveries or outcomes.",
                "If Source RAG conflicts with chapter script, direction or continuity, ignore the conflicting RAG material.",
                "Retrieval relevance does not imply narrative authority.",
                "Reference Bible controls how references are interpreted but never overrides project rules, story direction or project bibles.",
            ],
        },

        "priority_rules": [
            "operator_instruction",
            "chapter_plan",
            "anchor_facts",
            "continuity_ledger",
            "recent_chapter_context",
            "narrative_memory",
            "book_kit",
            "reference_bible",
            "source_rag",
            "creative_inference",
        ],
    }
    sections = {"anchor_facts": json.dumps(payload["anchor_facts"]), "chapter_plan": json.dumps(payload["chapter"]),
                "book_kit": json.dumps(kit), "continuity_ledger": json.dumps(payload["continuity"]),
                "history": json.dumps(payload["history"]["summaries"]), "recent_chapter_context": payload["history"]["recent_context"],
                "narrative_memory": json.dumps(memory),
                "reference_bible": project.reference_bible,
                "source_rag": json.dumps(source_rag, ensure_ascii=False)}
    budget = allocate_context(sections)
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
        source_rag_context=source_rag, narrative_memory_context=memory, payload_json=payload,
        payload_sha256=payload_sha, estimated_input_tokens=budget["estimated_input"], token_budget=budget)
