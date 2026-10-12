from __future__ import annotations

import json
import re
from pathlib import Path

from editorial.edition_renderer import EditionRenderer


def build_book_preview_context(edition, *, source_path: Path | None = None) -> dict:
    """Build the shared visual book-preview context used by Intake and Production."""
    renderer = EditionRenderer(edition, source_path=source_path)
    result = renderer.render(allow_preview_cover=True)
    state = renderer._load_state()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    pages = [
        path.removeprefix("text/")
        for path in result.spine
        if path.startswith("text/")
    ]
    page_labels = {
        "cover.xhtml": "Capa",
        "title_page.xhtml": "Folha de rosto",
        "copyright.xhtml": "Copyright",
        "about_this_edition.xhtml": "About This Book",
        "contents.xhtml": "Sumário",
        "frontispiece.xhtml": "Frontispício",
        "body_introduction.xhtml": "Introdução",
        "the_end.xhtml": "Fim",
    }
    page_links = []
    chapter_numbers: set[int] = set()
    illustrated_chapters: set[int] = set()
    for page in pages:
        chapter_match = re.fullmatch(r"chapter_(\d+)(_opening)?\.xhtml", page)
        if chapter_match:
            chapter_number = int(chapter_match.group(1))
            chapter_numbers.add(chapter_number)
            if chapter_match.group(2):
                illustrated_chapters.add(chapter_number)
                label = f"Capítulo {chapter_number:02d} · abertura ilustrada"
            else:
                label = f"Capítulo {chapter_number:02d} · texto"
        else:
            label = page_labels.get(page, page)
        page_links.append({"filename": page, "label": label})

    return {
        "render_result": result,
        "render_state": state,
        "manifest": manifest,
        "pages": pages,
        "page_links": page_links,
        "first_page": pages[0],
        "chapter_count": len(chapter_numbers),
        "illustrated_chapter_count": len(illustrated_chapters),
    }
