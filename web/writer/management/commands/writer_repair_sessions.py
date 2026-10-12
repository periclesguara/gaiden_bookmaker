"""Repair selected stored sessions once through Django's local Writer pipeline."""
from django.core.management.base import BaseCommand, CommandError

from web.writer.models import Chapter
from web.writer.services.repair import repair_chapter_sessions


class Command(BaseCommand):
    help = (
        "Repair existing sessions through Writer validation; preserves originals "
        "in immutable audit runs and stops on the first failure."
    )

    def add_arguments(self, parser):
        parser.add_argument("chapter_id", type=int)
        parser.add_argument("sessions", nargs="+", type=int)

    def handle(self, *args, **options):
        try:
            chapter = Chapter.objects.get(pk=options["chapter_id"])
            repair_chapter_sessions(chapter, options["sessions"])
        except Chapter.DoesNotExist as exc:
            raise CommandError("Capítulo não encontrado.") from exc
        except (ValueError, RuntimeError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"Chapter {chapter.pk}: repaired sessions "
            + ", ".join(str(number) for number in sorted(set(options["sessions"])))
        )
