from django.db import transaction

from ..models import Chapter, StoryProject
from .manuscripts import ensure_project_writable


@transaction.atomic
def synchronize_chapters(project: StoryProject) -> None:
    existing_numbers = set(project.chapters.values_list("number", flat=True))
    Chapter.objects.bulk_create([
        Chapter(project=project, number=number, title=f"Capítulo {number:02d}")
        for number in range(1, project.chapter_count + 1)
        if number not in existing_numbers
    ])


@transaction.atomic
def save_reference_bible(project: StoryProject, text: str) -> StoryProject:
    current = StoryProject.objects.select_for_update().get(pk=project.pk)
    ensure_project_writable(current)
    current.reference_bible = text
    current.save(update_fields=("reference_bible", "updated_at"))
    return current
