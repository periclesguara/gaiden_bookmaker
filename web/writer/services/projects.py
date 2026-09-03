from django.db import transaction

from ..models import Chapter, StoryProject


@transaction.atomic
def synchronize_chapters(project: StoryProject) -> None:
    existing_numbers = set(project.chapters.values_list("number", flat=True))
    Chapter.objects.bulk_create([
        Chapter(project=project, number=number, title=f"Capítulo {number:02d}")
        for number in range(1, project.chapter_count + 1)
        if number not in existing_numbers
    ])
