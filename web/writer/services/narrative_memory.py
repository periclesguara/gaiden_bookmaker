from __future__ import annotations

import hashlib


def retrieve_narrative_memory(project, chapter_number: int, query: str = "", top_k: int = 8) -> list[dict]:
    rows = []
    for chapter in project.chapters.filter(number__lt=chapter_number).order_by("-number"):
        text = (chapter.final_text or chapter.generated_text).strip()
        if not text:
            continue
        rows.append({"context_type": "NARRATIVE_MEMORY", "chapter": chapter.number,
                     "text": text, "score": 1.0 if query.casefold() in text.casefold() else 0.1,
                     "sha256": hashlib.sha256(text.encode()).hexdigest()})
    return sorted(rows, key=lambda row: (-row["score"], -row["chapter"]))[:top_k]
