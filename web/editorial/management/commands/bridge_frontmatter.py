from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from editorial.migrations_bridge import migrate_all_languages_for_book
from editorial.models import Edition
from pipeline.services import paths


class Command(BaseCommand):
    help = (
        "Faz a ponte entre arquivos de frontmatter (*.md) e o modelo Edition.\n"
        "Le os 4 arquivos por lingua (frontispiece.md, copyright.md, "
        "about_edition.md, about_contributor.md), limpa heading/pagebreak, "
        "e grava nos campos de template da Edition."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "book_code",
            nargs="?",
            type=str,
            help="Codigo do livro, ex: book01_the_adventures_of_sherlock_holmes",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Rodar para todos os book_codes existentes em Edition ou data/frontmatter/",
        )

    def handle(self, *args, **options):
        book_code = options.get("book_code")
        run_all = options.get("all")

        if not book_code and not run_all:
            raise CommandError(
                "Informe um book_code ou use --all.\n\n"
                "Exemplos:\n"
                "  python manage.py bridge_frontmatter book01_the_adventures_of_sherlock_holmes\n"
                "  python manage.py bridge_frontmatter --all\n"
            )

        if run_all:
            self._handle_all()
        else:
            self._handle_single(book_code)

    def _handle_single(self, book_code: str):
        self.stdout.write(self.style.MIGRATE_HEADING(f"Bridge frontmatter para: {book_code}"))
        editions = migrate_all_languages_for_book(book_code)
        if not editions:
            self.stdout.write(
                self.style.WARNING(f"Nenhuma lingua encontrada em data/frontmatter/{book_code}/")
            )
            return

        counter = Counter()
        for ed in editions:
            counter[ed.language.code] += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"  OK -> Edition(book_code={ed.work.code}, language={ed.language.code})"
                )
            )

        self.stdout.write(self.style.HTTP_INFO("Resumo por lingua:"))
        for lang, count in counter.items():
            self.stdout.write(f"  {lang}: {count} Edition(s) atualizada(s)")

    def _handle_all(self):
        book_codes = (
            Edition.objects.values_list("work__code", flat=True)
            .distinct()
        )

        if not book_codes:
            base_dir = paths.data_dir() / "frontmatter"
            if base_dir.exists():
                book_codes = sorted({p.name for p in base_dir.iterdir() if p.is_dir()})

        if not book_codes:
            self.stdout.write(self.style.WARNING("Nenhuma Edition encontrada no banco."))
            return

        global_counter = Counter()

        for bc in book_codes:
            self.stdout.write(self.style.MIGRATE_HEADING(f"Bridge frontmatter para: {bc}"))
            editions = migrate_all_languages_for_book(bc)
            if not editions:
                self.stdout.write(
                    self.style.WARNING(
                        f"  Nenhum frontmatter encontrado em data/frontmatter/{bc}/ (ignorando)."
                    )
                )
                continue

            local_counter = Counter()
            for ed in editions:
                local_counter[ed.language.code] += 1
                global_counter[ed.language.code] += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  OK -> Edition(book_code={ed.work.code}, language={ed.language.code})"
                    )
                )

            self.stdout.write(self.style.HTTP_INFO("Resumo por lingua (este book_code):"))
            for lang, count in local_counter.items():
                self.stdout.write(f"  {lang}: {count} Edition(s)")

        self.stdout.write(self.style.MIGRATE_HEADING("Resumo global por lingua:"))
        for lang, count in global_counter.items():
            self.stdout.write(f"  {lang}: {count} Edition(s) no total")
