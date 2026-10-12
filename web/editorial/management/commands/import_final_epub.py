from django.core.management.base import BaseCommand, CommandError

from gaiden.application.builds.final_epub_import import FinalEpubImportError, import_final_epub


class Command(BaseCommand):
    help = "Validate and atomically register an externally approved final EPUB."

    def add_arguments(self, parser):
        parser.add_argument("--edition-id", type=int, required=True)
        parser.add_argument("--locale", required=True)
        parser.add_argument("--file", required=True)
        parser.add_argument("--sha256", required=True)
        parser.add_argument("--size", type=int, required=True)
        parser.add_argument("--source", default="EXTERNAL_FINAL_UPLOAD")
        parser.add_argument("--actor", default="management-command")
        parser.add_argument("--official-body")
        parser.add_argument("--approve", action="store_true")

    def handle(self, *args, **options):
        try:
            result = import_final_epub(
                edition_id=options["edition_id"],
                locale=options["locale"],
                source_path=options["file"],
                expected_sha256=options["sha256"],
                expected_size_bytes=options["size"],
                source=options["source"],
                actor=options["actor"],
                official_body_path=options.get("official_body"),
                approved=options["approve"],
            )
        except FinalEpubImportError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"{result.outcome}: build_id={result.build.id} v{result.build.build_version} {result.destination}"
        ))
