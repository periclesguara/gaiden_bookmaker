from __future__ import annotations

import subprocess

from django.core.management.base import BaseCommand, CommandError

from editorial.edition_renderer import EditionRenderer
from editorial.models import Edition


class Command(BaseCommand):
    help = "Render, approve and package the canonical gaiden_epub_premium EPUB."

    def add_arguments(self, parser):
        parser.add_argument("--edition-id", type=int, required=True)
        parser.add_argument("--approve", action="store_true")
        parser.add_argument("--build", action="store_true")
        parser.add_argument("--epubcheck", action="store_true")
        parser.add_argument("--filename", default="BOOK.epub")

    def handle(self, *args, **options):
        try:
            edition = Edition.objects.select_related("work", "language", "seal", "work__author").get(
                pk=options["edition_id"]
            )
        except Edition.DoesNotExist as exc:
            raise CommandError(f"Edition {options['edition_id']} not found") from exc

        renderer = EditionRenderer(edition)
        result = renderer.render()
        self.stdout.write(f"preview={result.root}")
        self.stdout.write(f"fingerprint={result.fingerprint}")

        if options["approve"]:
            result = renderer.approve_preview()
            self.stdout.write(self.style.SUCCESS(f"approved={result.fingerprint}"))

        epub_path = None
        if options["build"] or options["epubcheck"]:
            try:
                epub_path = renderer.build_epub(options["filename"], require_approval=True)
            except Exception as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(self.style.SUCCESS(f"epub={epub_path}"))

        if options["epubcheck"]:
            result = subprocess.run(
                ["epubcheck", str(epub_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode:
                raise CommandError(f"EPUBCheck failed:\n{result.stdout}\n{result.stderr}")
            self.stdout.write(self.style.SUCCESS("epubcheck=PASS"))
            if result.stdout.strip():
                self.stdout.write(result.stdout.strip())
