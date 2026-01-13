from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from pipeline.models import BookEditionTemplate, PipelineJob


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
            edition = BookEditionTemplate.objects.get(
                book_code=book_code,
                language=language,
            )
        except BookEditionTemplate.DoesNotExist:
            raise CommandError(
                f"Nenhuma BookEditionTemplate encontrada para book_code={book_code!r}, language={language!r}."
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
        front_files = {
            "frontispiece.md",
            "copyright.md",
            "about_edition.md",
            "about_contributor.md",
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

        stage_priority = ["polish", "refine", "translate"]
        content_job = None

        for stage in stage_priority:
            qs = (
                PipelineJob.objects.filter(
                    book_code=book_code,
                    language=language,
                    stage=stage,
                    status="SUCCESS",
                )
                .order_by("-updated_at")
            )
            if qs.exists():
                content_job = qs.first()
                break

        if not content_job:
            raise CommandError(
                f"Nenhum PipelineJob SUCCESS encontrado para {book_code} [{language}] "
                f"nas etapas {stage_priority}."
            )

        content_path = Path(content_job.filepath)
        if not content_path.exists():
            raise CommandError(
                f"Arquivo de miolo nao encontrado: {content_path} (do job id={content_job.id})."
            )

        def read_file(path: Path) -> str:
            return path.read_text(encoding="utf-8")

        frontispiece = read_file(front_dir / "frontispiece.md")
        copyright_text = read_file(front_dir / "copyright.md")
        about_edition = read_file(front_dir / "about_edition.md")
        about_contributor = read_file(front_dir / "about_contributor.md")
        content = read_file(content_path)

        sections = []
        header_comment = (
            f"<!--\n"
            f"  book_code: {edition.book_code}\n"
            f"  language: {edition.language}\n"
            f"  title: {edition.title}\n"
            f"  author: {edition.author_name}\n"
            f"  collaborator: {edition.collaborator_name} ({edition.collaborator_roles})\n"
            f"  year: {edition.publication_year}\n"
            f"-->\n\n"
        )
        sections.append(header_comment)
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
                f"Conteudo base: frontmatter em {front_dir} + miolo ({content_job.stage}) em {content_path}"
            )
        )
