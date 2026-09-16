from __future__ import annotations

import hashlib
import json

from django.utils import timezone

from ..models import ChapterNarrativeSummary, StoryContinuityState


def close_chapter(chapter, contract=None) -> tuple[ChapterNarrativeSummary, StoryContinuityState]:
    text = (chapter.final_text or chapter.generated_text).strip()
    if not text:
        raise ValueError("chapter must contain generated text before closure")
    previous = chapter.project.continuity_states.order_by("-through_chapter", "-version").first()
    state = dict(previous.state_json) if previous else {"schema_version": "1.0", "plot": {"open_threads": []}}
    state.setdefault("plot", {}).setdefault("established_facts", [])
    state["plot"]["last_closed_chapter"] = chapter.number
    digest = hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    summary = ChapterNarrativeSummary.objects.create(
        project=chapter.project, chapter=chapter, short_summary=text[:500],
        detailed_summary=text[:4000], major_events=[], closing_state=state,
        continuity_delta={"new_facts": [], "resolved_threads": [], "new_threads": [],
                          "continuity_notes": []},
        source_contract_sha256=chapter.generation_contract_sha256,
    )
    ledger = StoryContinuityState.objects.create(
        project=chapter.project, through_chapter=chapter.number, state_json=state,
        state_sha256=digest,
    )
    return summary, ledger
