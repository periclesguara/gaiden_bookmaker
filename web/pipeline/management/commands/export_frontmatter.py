from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from editorial.models import Edition as EditorialEdition
from editorial.frontmatter import build_frontmatter_files


class Command(BaseCommand):
    help = "Exporta Title Page/copyright/source record/about* a partir de Edition."

    def add_arguments(self, parser):
        parser.add_argument(
            "--book-code",
            dest="book_code",
            help="Codigo do livro (ex: book_0001). Se omitido, exporta todas as edicoes.",
        )
        parser.add_argument(
            "--language",
            dest="language",
            help="Lingua (ex: en, ptbr, es, de). Se usada com --book-code, filtra essa edicao.",
        )
        parser.add_argument(
            "--base-dir",
            dest="base_dir",
            help="Diretorio base para salvar (default: <project_root>/data/frontmatter).",
        )

    def handle(self, *args, **options):
        book_code = options.get("book_code")
        language = options.get("language")
        base_dir_opt = options.get("base_dir")

        project_root = Path(__file__).resolve().parents[4]
        base_dir = Path(base_dir_opt) if base_dir_opt else project_root / "data" / "frontmatter"

        qs = EditorialEdition.objects.select_related("work", "language").all()
        if book_code:
            qs = qs.filter(work__code=book_code)
        if language:
            qs = qs.filter(language__code=language)

        if not qs.exists():
            msg = "Nenhuma edicao encontrada para exportar."
            if book_code:
                msg += f" book_code={book_code!r}"
            if language:
                msg += f" language={language!r}"
            raise CommandError(msg)

        self.stdout.write(
            self.style.NOTICE(
                f"Exportando frontmatter para {qs.count()} edicoes em {base_dir}"
            )
        )

        for edition in qs:
            build_frontmatter_files(edition, base_dir)
            self.stdout.write(
                self.style.SUCCESS(
                    f"[{edition.work.code} {edition.language.code}] -> {base_dir / edition.work.code / edition.language.code}"
                )
            )

        self.stdout.write(self.style.SUCCESS("Export de frontmatter concluido."))
