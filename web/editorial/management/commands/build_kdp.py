from django.core.management.base import BaseCommand, CommandError

from editorial.kdp_mode import (
    build_epub_for_edition,
    build_frontmatter_files,
    build_merged_kdp_source,
    builds_dir,
    frontmatter_dir,
)
from editorial.models import Edition


class Command(BaseCommand):
    help = (
        "Build KDP-mode para uma Edition (book_code + language):\n"
        " - Gera frontmatter/*.md\n"
        " - Gera kdp_merged.md + BOOK.BUILD.MD\n"
        " - Exporta ebook.epub via Pandoc\n"
        "\n"
        "Exemplo:\n"
        "  python manage.py build_kdp book01_the_adventures_of_sherlock_holmes de\n"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "book_code",
            type=str,
            help="Codigo do livro, ex: book01_the_adventures_of_sherlock_holmes",
        )
        parser.add_argument(
            "language",
            type=str,
            help="Idioma da Edition (en, de, es, ptbr, ...)",
        )
        parser.add_argument(
            "--no-epub",
            action="store_true",
            help="Rodar build apenas ate o BOOK.BUILD.MD (nao gerar EPUB).",
        )

    def handle(self, *args, **options):
        book_code = options["book_code"]
        language = options["language"]
        no_epub = options["no_epub"]

        try:
            edition = Edition.objects.get(work__code=book_code, language__code=language)
        except Edition.DoesNotExist:
            raise CommandError(f"Edition nao encontrada: {book_code} [{language}]")

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Build KDP para {book_code} [{language}]"
        ))

        build_frontmatter_files(edition, frontmatter_dir(edition).parent)
        merged_path = build_merged_kdp_source(edition)
        book_build_path = builds_dir(edition) / "BOOK.BUILD.MD"

        self.stdout.write(self.style.SUCCESS(f"  frontmatter   -> {frontmatter_dir(edition)}"))
        self.stdout.write(self.style.SUCCESS(f"  kdp_merged.md -> {merged_path}"))
        self.stdout.write(self.style.SUCCESS(f"  BOOK.BUILD.MD -> {book_build_path}"))

        if no_epub:
            self.stdout.write(self.style.HTTP_INFO("EPUB nao gerado (--no-epub ativo)."))
            return

        try:
            epub_path = build_epub_for_edition(edition, epub_filename="BOOK.epub")
        except RuntimeError as exc:
            raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS(f"  ebook.epub    -> {epub_path}"))
        self.stdout.write(self.style.HTTP_INFO("Build KDP finalizado com sucesso."))
