from django.core.management.base import BaseCommand, CommandError

from editorial.models import Edition
from pipeline.services.rinobooks_publish import RinoBooksPublishError, publish_edition


class Command(BaseCommand):
    help = "Validate and send one completed EPUB edition to RinoBooks as a draft."

    def add_arguments(self, parser):
        parser.add_argument("--edition-id", type=int, required=True)

    def handle(self, *args, **options):
        try:
            edition = Edition.objects.select_related(
                "work",
                "work__author",
                "language",
            ).get(pk=options["edition_id"])
        except Edition.DoesNotExist as exc:
            raise CommandError("Edition not found") from exc

        try:
            draft = publish_edition(edition, export_user="management-command")
        except RinoBooksPublishError as exc:
            raise CommandError(str(exc)) from exc

        flags = []
        if draft.duplicate:
            flags.append("duplicate")
        if draft.replaced_draft:
            flags.append("replaced draft")
        suffix = f" ({', '.join(flags)})" if flags else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"RinoBooks draft {draft.edition_id}: {draft.status}{suffix}"
            )
        )
