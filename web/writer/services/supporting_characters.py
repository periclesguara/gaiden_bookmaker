from __future__ import annotations

import json
import os
import re
from typing import Any

from gaiden.writer_engine.clients import QwenGenerator
from writer.models import StoryProject

SCHEMA_VERSION = 1
MIN_CHARACTERS = 3
MAX_CHARACTERS = 12
_CHARACTER_ID = re.compile(r"^SUP-\d{3}$")


def _generator() -> QwenGenerator:
    return QwenGenerator(
        base_url=os.environ.get("GAIDEN_QWEN_BASE_URL", "http://127.0.0.1:8000/v1"),
        api_key=os.environ.get("GAIDEN_QWEN_API_KEY", "placeholder"),
        model=os.environ.get("GAIDEN_QWEN_MODEL", "Qwen/Qwen3.5-9B"),
        temperature=0.4,
        thinking=os.environ.get("GAIDEN_QWEN_THINKING", "0").casefold()
        in {"1", "true", "yes", "on"},
    )


def _clip(value: str, limit: int = 8_000) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[:limit] + "\n[truncated]"


def _project_context(project: StoryProject) -> str:
    chapter_rows = []
    for chapter in project.chapters.order_by("number"):
        chapter_rows.append(
            f"Chapter {chapter.number}: {chapter.title or '(untitled)'}\n"
            f"Direction: {_clip(chapter.direction, 1_500) or '(pending)'}\n"
            f"Script: {_clip(chapter.script, 2_500) or '(pending)'}"
        )
    return (
        f"Project title:\n{_clip(project.title)}\n\n"
        f"Output language:\n{project.language_contract.get('target_language', project.language)}\n\n"
        f"Premise:\n{_clip(project.premise)}\n\n"
        f"Protagonist bible:\n{_clip(project.character_bible)}\n\n"
        f"Antagonist bible:\n{_clip(project.antagonist_bible)}\n\n"
        f"Scenarios and locations:\n{_clip(project.scenario_bible)}\n\n"
        f"World, period, climate and references:\n{_clip(project.world_bible)}\n\n"
        f"Story direction:\n{_clip(project.story_direction)}\n\n"
        f"Story outline:\n{_clip(project.story_outline)}\n\n"
        f"Chapter plan:\n{_clip(chr(10).join(chapter_rows), 40_000)}"
    )


def _parse_json_object(raw: str) -> dict[str, Any]:
    start = raw.find("{")
    if start < 0:
        raise ValueError("Qwen did not return a JSON object")
    try:
        value, _ = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError as exc:
        raise ValueError("Qwen returned invalid JSON for the supporting cast") from exc
    if not isinstance(value, dict):
        raise ValueError("the supporting cast registry must be a JSON object")
    return value


def _required_text(character: dict[str, Any], field: str, character_id: str) -> str:
    value = character.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{character_id}: {field} must be non-empty text")
    return value.strip()


def _text_list(character: dict[str, Any], field: str, character_id: str) -> list[str]:
    value = character.get(field)
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{character_id}: {field} must be a list of non-empty texts")
    return [item.strip() for item in value]


def validate_supporting_characters_registry(
    registry: dict[str, Any], *, chapter_count: int
) -> dict[str, Any]:
    if registry.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    characters = registry.get("characters")
    if not isinstance(characters, list) or not MIN_CHARACTERS <= len(characters) <= MAX_CHARACTERS:
        raise ValueError(
            f"the supporting cast must contain {MIN_CHARACTERS} to {MAX_CHARACTERS} characters"
        )

    normalized: list[dict[str, Any]] = []
    identities: set[str] = set()
    ids: set[str] = set()
    required_lists = (
        "aliases",
        "physical_markers",
        "traits",
        "relationships",
        "knowledge_limits",
        "continuity_rules",
    )
    required_texts = ("name", "role", "voice", "goal")

    for position, raw_character in enumerate(characters, start=1):
        if not isinstance(raw_character, dict):
            raise ValueError(f"supporting character {position} must be an object")
        character_id = raw_character.get("character_id")
        expected_id = f"SUP-{position:03d}"
        if (
            not isinstance(character_id, str)
            or not _CHARACTER_ID.fullmatch(character_id)
            or character_id != expected_id
        ):
            raise ValueError(
                f"supporting character {position} must use sequential ID {expected_id}"
            )
        if character_id in ids:
            raise ValueError(f"duplicate supporting character ID: {character_id}")
        ids.add(character_id)

        character: dict[str, Any] = {"character_id": character_id}
        for field in required_texts:
            character[field] = _required_text(raw_character, field, character_id)
        for field in required_lists:
            character[field] = _text_list(raw_character, field, character_id)

        chapters = raw_character.get("chapters")
        if (
            not isinstance(chapters, list)
            or not chapters
            or any(
                isinstance(number, bool)
                or not isinstance(number, int)
                or number < 1
                or number > chapter_count
                for number in chapters
            )
        ):
            raise ValueError(
                f"{character_id}: chapters must contain valid chapter numbers"
            )
        character["chapters"] = sorted(set(chapters))

        for identity in [character["name"], *character["aliases"]]:
            identity_key = identity.casefold()
            if identity_key in identities:
                raise ValueError(f"duplicate supporting character name or alias: {identity}")
            identities.add(identity_key)
        normalized.append(character)

    return {"schema_version": SCHEMA_VERSION, "characters": normalized}


def generate_supporting_characters_bible(project: StoryProject) -> str:
    if project.chapters.filter(sessions__isnull=False).exists():
        raise ValueError(
            "the supporting cast cannot be regenerated after chapter generation has started"
        )
    required = {
        "premise": project.premise,
        "protagonist bible": project.character_bible,
        "antagonist bible": project.antagonist_bible,
        "scenarios and locations": project.scenario_bible,
        "world and period": project.world_bible,
        "story direction": project.story_direction,
        "story outline": project.story_outline,
    }
    missing = [label for label, value in required.items() if not value.strip()]
    if missing:
        raise ValueError("complete before generating the supporting cast: " + ", ".join(missing))

    system = (
        "You are the continuity architect for a novel. Create a supporting-cast registry, "
        "not prose. Keep every identity distinct from the protagonist and antagonist. "
        "Return JSON only, with no Markdown. Use the project's output language for values "
        "and preserve the English JSON keys exactly."
    )
    user = f"""Create {MIN_CHARACTERS} to {MAX_CHARACTERS} supporting characters only when the
story needs them. Assign sequential stable IDs SUP-001, SUP-002, and so on. Do not create
one character per chapter. Reuse characters causally across chapters when appropriate.

Return exactly:
{{
  "schema_version": 1,
  "characters": [
    {{
      "character_id": "SUP-001",
      "name": "canonical name",
      "aliases": ["alias"],
      "role": "story function and occupation",
      "physical_markers": ["stable identifying detail"],
      "traits": ["stable personality trait"],
      "voice": "speech pattern and mannerisms",
      "goal": "personal objective",
      "relationships": ["explicit relationship to a named character"],
      "knowledge_limits": ["what this character knows or must not know"],
      "continuity_rules": ["facts that must never change or be confused"],
      "chapters": [1]
    }}
  ]
}}

Rules:
- Names, aliases, roles, voices, and physical markers must be distinguishable.
- Never merge two identities or transfer relationships, knowledge, or traits.
- Chapter numbers must be between 1 and {project.chapter_count}.
- Include only characters with a clear narrative function.
- Do not duplicate the protagonist or antagonist under another identity.

PROJECT:
{_project_context(project)}
"""
    raw = _generator().generate(system=system, user=user, max_tokens=6_000)
    registry = validate_supporting_characters_registry(
        _parse_json_object(raw), chapter_count=project.chapter_count
    )
    serialized = json.dumps(registry, ensure_ascii=False, indent=2)
    project.supporting_characters_bible = serialized
    project.save(update_fields=("supporting_characters_bible", "updated_at"))
    return serialized


def supporting_characters_context(
    raw: str, *, chapter_number: int, chapter_count: int
) -> str:
    if not raw.lstrip().startswith("{"):
        return (
            "Supporting characters (legacy free text):\n"
            f"{raw}\n\n"
            "Continuity lock: never merge identities or transfer names, traits, "
            "relationships, roles, or knowledge between supporting characters."
        )
    registry = validate_supporting_characters_registry(
        _parse_json_object(raw), chapter_count=chapter_count
    )
    characters = registry["characters"]

    identity_map = []
    relevant = []
    for character in characters:
        if not isinstance(character, dict):
            continue
        identity_map.append(
            {
                "character_id": character.get("character_id"),
                "name": character.get("name"),
                "aliases": character.get("aliases", []),
                "role": character.get("role"),
            }
        )
        if chapter_number in character.get("chapters", []):
            relevant.append(character)
    return (
        "Supporting-character identity map (global; never merge or reassign):\n"
        f"{json.dumps(identity_map, ensure_ascii=False)}\n\n"
        f"Supporting characters authorized for chapter {chapter_number}:\n"
        f"{json.dumps(relevant, ensure_ascii=False, indent=2)}\n\n"
        "Continuity lock: use canonical names and IDs. Do not transfer aliases, "
        "traits, relationships, roles, goals, or knowledge between characters. "
        "Do not introduce an unlisted supporting character as if already established."
    )
