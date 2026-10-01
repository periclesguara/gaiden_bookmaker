"""Local, immutable project-level manuscript merge."""

from __future__ import annotations

import hashlib
from pathlib import Path

from django.db import transaction

from ..models import Chapter, ManuscriptRevision, StoryProject
from .manuscripts import (_immutable_write, _revision_path, detect_chapter_headings,
                         ensure_project_writable, revision_bytes)


def _final_chapters(project: StoryProject) -> list[Chapter]:
    chapters = list(project.chapters.order_by("number"))
    if len(chapters) != project.chapter_count:
        raise ValueError(
            f"todos os {project.chapter_count} capítulos precisam existir antes de salvar o manuscrito"
        )
    incomplete = [
        chapter.number
        for chapter in chapters
        if chapter.status != Chapter.Status.FINAL or not chapter.final_text.strip()
    ]
    if incomplete:
        numbers = ", ".join(f"{number:02d}" for number in incomplete)
        raise ValueError(f"capítulos ainda não finalizados: {numbers}")
    return chapters


def _merged_text(project: StoryProject, chapters: list[Chapter]) -> str:
    sections = []
    for chapter in chapters:
        heading = f"# Chapter {chapter.number:02d}"
        if chapter.title.strip():
            heading += f" — {chapter.title.strip()}"
        sections.append(f"{heading}\n\n{chapter.final_text.strip()}".strip())
    return "\n\n".join(sections) + "\n"


@transaction.atomic
def save_merged_manuscript(project: StoryProject, created_by=None) -> ManuscriptRevision:
    """Persist one revision containing every finalized chapter in order."""
    project = StoryProject.objects.select_for_update().get(pk=project.pk)
    ensure_project_writable(project)
    chapters = _final_chapters(project)
    text = _merged_text(project, chapters)
    encoded = text.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()

    current = project.manuscript_merge_revision
    if current and current.sha256 == digest:
        revision_bytes(project, current)
        return current

    version = (
        ManuscriptRevision.objects.filter(project=project)
        .order_by("-version")
        .values_list("version", flat=True)
        .first()
        or 0
    ) + 1
    path = _revision_path(project, version)
    _immutable_write(path, encoded)
    try:
        revision = ManuscriptRevision.objects.create(
            project=project,
            version=version,
            source_kind=ManuscriptRevision.SourceKind.CHAPTER_MERGE,
            original_filename=f"{project.title[:220]}_MERGED.md",
            storage_path=str(path),
            sha256=digest,
            word_count=len(text.split()),
            character_count=len(text),
            detected_chapter_count=len(detect_chapter_headings(text)),
            created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
        )
        project.manuscript_merge_revision = revision
        project.save(update_fields=("manuscript_merge_revision", "updated_at"))
        return revision
    except BaseException:
        path.unlink(missing_ok=True)
        raise

