"""One explicit local-model merge for validated scenes; no Qwen/Phi stages."""
from __future__ import annotations

from .clients import Generator

MAX_SCENE_CHARS = 80_000


def merge_scenes(generator: Generator, *, language: str, chapter_number: int,
                  chapter_title: str, scene_texts: list[str], target_words: int) -> str:
    if not scene_texts or any(not text.strip() or len(text) > MAX_SCENE_CHARS for text in scene_texts):
        raise ValueError("complete bounded scene sessions are required")
    system = (
        f"Write in {language}. Join the supplied ordered scenes into one continuous chapter. "
        "Preserve their events, characters, chronology and wording. Do not invent events, "
        "summaries, narrative memory, or an audit. Return only the merged chapter."
    )
    user = (f"Chapter {chapter_number}: {chapter_title}\nTarget length: approximately {target_words} words.\n\n"
            + "\n\n".join(f"<scene index=\"{i}\">\n{text}\n</scene>" for i, text in enumerate(scene_texts, 1)))
    return generator.generate(system=system, user=user, max_tokens=min(32768, max(2048, target_words * 2))).strip()
