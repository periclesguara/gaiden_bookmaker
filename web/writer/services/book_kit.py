from __future__ import annotations

import hashlib
import json


def _canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_book_kit(project) -> dict:
    kit = {
        "schema_version": "1.0", "project_id": project.pk, "title": project.title,
        "writing_mode": "FICTION", "language": project.language,
        "premise": project.premise,
        "bibles": {"protagonist": project.character_bible, "antagonist": project.antagonist_bible,
                   "supporting_characters": project.supporting_characters_bible,
                   "scenario": project.scenario_bible, "world": project.world_bible},
        "story_direction": project.story_direction, "story_outline": project.story_outline,
        "language_contract": {"language": project.language, "prose_only": True},
        "anchor_facts": list(project.anchor_facts or []), "global_constraints": [],
    }
    kit["sha256"] = hashlib.sha256(_canonical(kit).encode()).hexdigest()
    return kit
