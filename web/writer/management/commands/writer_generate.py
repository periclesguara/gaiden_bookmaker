"""Run one complete pending chapter once, entirely inside Django."""
from django.core.management.base import BaseCommand, CommandError
from web.writer.models import Chapter
from web.writer.services.narrative import run_chapter_workflow


class Command(BaseCommand):
    help = "Generate one complete chapter once; stop on any error without retry."

    def add_arguments(self, parser):
        parser.add_argument("chapter_id", type=int)

    def handle(self, *args, **options):
        try:
            chapter = Chapter.objects.get(pk=options["chapter_id"])
            run_chapter_workflow(chapter)
        except (Chapter.DoesNotExist, ValueError, RuntimeError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Chapter {chapter.pk}: {chapter.status}")
