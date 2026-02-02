from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from editorial.models import Edition as EditorialEdition
from pipeline.services import paths


class Command(BaseCommand):
    help = "Monta um arquivo unico (frontmatter + miolo) a partir do DB e do pipeline."

    def add_arguments(self, parser):
        parser.add_argument(
            "--book-code",
            dest="book_code",
            required=True,
            help="Codigo do livro (ex: book_0001).",
        )
        parser.add_argument(
            "--language",
            dest="language",
            required=True,
            help="Lingua (ex: en, ptbr, es, de).",
        )
        parser.add_argument(
            "--output",
            dest="output",
            help=(
                "Caminho completo do arquivo de saida. "
                "Se omitido, usa data/builds/<book_code>/<language>/<book_code>_<language>_book.md"
            ),
        )

    def handle(self, *args, **options):
        book_code = options["book_code"]
        language = options["language"]
        output_opt = options.get("output")

        project_root = Path(__file__).resolve().parents[4]

        try:
            edition = EditorialEdition.objects.get(
                work__code=book_code,
                language__code=language,
            )
        except EditorialEdition.DoesNotExist:
            raise CommandError(
                f"Nenhuma Edition encontrada para book_code={book_code!r}, language={language!r}."
            )

        frontmatter_base = project_root / "data" / "frontmatter"
        builds_base = project_root / "data" / "builds"

        if output_opt:
            output_path = Path(output_opt)
        else:
            output_dir = builds_base / book_code / language
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{book_code}_{language}_book.md"

        self.stdout.write(
            self.style.NOTICE(
                f"Montando livro para {book_code} [{language}] em {output_path}"
            )
        )

        front_dir = frontmatter_base / book_code / language
        # Only these are required to build a minimal book file.
        front_files = {
            "frontispiece.md",
            "copyright.md",
        }

        if not front_dir.exists() or not all((front_dir / f).exists() for f in front_files):
            self.stdout.write(
                self.style.WARNING(
                    "Frontmatter nao encontrado completo. Rodando export_frontmatter..."
                )
            )
            call_command(
                "export_frontmatter",
                f"--book-code={book_code}",
                f"--language={language}",
            )

        if not front_dir.exists() or not all((front_dir / f).exists() for f in front_files):
            raise CommandError(
                f"Mesmo apos export_frontmatter, frontmatter esta incompleto em {front_dir}"
            )

        candidates = [
            paths.miolo_md_path_for_language(book_code, language),
        ]
        content_path = next((p for p in candidates if p.exists()), None)
        if not content_path:
            raise CommandError(
                f"Nenhum {paths.miolo_md_filename()} encontrado para {book_code} [{language}]."
            )

        def read_file(path: Path) -> str:
            return path.read_text(encoding="utf-8")

        frontispiece = read_file(front_dir / "frontispiece.md")
        copyright_text = read_file(front_dir / "copyright.md")
        about_edition_path = front_dir / "about_edition.md"
        about_contributor_path = front_dir / "about_contributor.md"
        about_edition = read_file(about_edition_path) if about_edition_path.exists() else ""
        about_contributor = read_file(about_contributor_path) if about_contributor_path.exists() else ""
        content = read_file(content_path)

        sections = []
        sections.append(frontispiece.strip() + "\n\n---\n\n")
        sections.append(copyright_text.strip() + "\n\n---\n\n")

        if about_edition.strip():
            sections.append(about_edition.strip() + "\n\n---\n\n")
        if about_contributor.strip():
            sections.append(about_contributor.strip() + "\n\n---\n\n")

        sections.append(content.strip() + "\n")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("".join(sections), encoding="utf-8")

        self.stdout.write(self.style.SUCCESS(f"Livro montado em {output_path}"))
        self.stdout.write(
            self.style.SUCCESS(
                f"Conteudo base: frontmatter em {front_dir} + miolo em {content_path}"
            )
        )
