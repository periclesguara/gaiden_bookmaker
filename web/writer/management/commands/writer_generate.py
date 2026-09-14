"""Run pending Writer sessions once, entirely inside Django."""
from django.core.management.base import BaseCommand, CommandError
from web.writer.models import Chapter
from web.writer.services.generation import generate_chapter
from web.writer.services.narrative import run_chapter_workflow


class Command(BaseCommand):
    help = "Generate only missing sessions once; stop on any error without retry."

    def add_arguments(self, parser):
        parser.add_argument("chapter_id", type=int)
        parser.add_argument(
            "--session",
            dest="sessions",
            action="append",
            type=int,
            help="Generate only this session; repeat the option to select more sessions.",
        )

    def handle(self, *args, **options):
        try:
            chapter = Chapter.objects.get(pk=options["chapter_id"])
            if options["sessions"]:
                generate_chapter(chapter, session_numbers=options["sessions"])
            else:
                run_chapter_workflow(chapter)
        except (Chapter.DoesNotExist, ValueError, RuntimeError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Chapter {chapter.pk}: {chapter.status}")
