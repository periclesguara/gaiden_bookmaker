from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from pipeline.models import BookEditionTemplate


class Command(BaseCommand):
    help = "Exporta frontispicio/copyright/about* para .md a partir de BookEditionTemplate."

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

        qs = BookEditionTemplate.objects.all()
        if book_code:
            qs = qs.filter(book_code=book_code)
        if language:
            qs = qs.filter(language=language)

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
            target_dir = base_dir / edition.book_code / edition.language
            target_dir.mkdir(parents=True, exist_ok=True)

            context_info = (
                f"# {edition.book_code} [{edition.language}] - {edition.title}\n"
                f"# author: {edition.author_name}\n"
                f"# collaborator: {edition.collaborator_name} ({edition.collaborator_roles})\n"
                f"# pseudonym: {edition.collaborator_pseudonym}\n"
                f"# year: {edition.publication_year}\n\n"
            )

            files = {
                "frontispiece.md": edition.frontispiece_rendered,
                "copyright.md": edition.copyright_rendered,
                "about_edition.md": edition.about_edition_rendered,
                "about_contributor.md": edition.about_contributor_rendered,
            }

            for filename, content in files.items():
                target_path = target_dir / filename
                text = context_info + (content or "")
                target_path.write_text(text, encoding="utf-8")
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[{edition.book_code} {edition.language}] -> {target_path}"
                    )
                )

        self.stdout.write(self.style.SUCCESS("Export de frontmatter concluido."))
