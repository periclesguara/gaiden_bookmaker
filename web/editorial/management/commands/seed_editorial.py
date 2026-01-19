from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from editorial.models import (
    Contributor,
    ContributorRole,
    Edition,
    EditionPipeline,
    EditionText,
    Language,
    PipelineStage,
    Seal,
    Work,
)


class Command(BaseCommand):
    help = "Cria os registros iniciais de Language/Work/Edition para uma obra."

    def add_arguments(self, parser):
        parser.add_argument("--code", required=True, help="Codigo da obra (ex: book01_the_adventures_of_sherlock_holmes)")
        parser.add_argument("--title", required=True, help="Titulo da obra")
        parser.add_argument("--author", required=True, help="Autor da obra")
        parser.add_argument("--language", default="en", help="Idioma da edicao (ex: en)")
        parser.add_argument("--seal", default="default", help="Selo/colecao (slug)")
        parser.add_argument("--publisher", default="", help="Editora")
        parser.add_argument("--year", type=int, default=None, help="Ano da obra")
        parser.add_argument("--edition-year", type=int, default=None, help="Ano da edicao")
        parser.add_argument(
            "--raw-path",
            default="",
            help="Caminho do arquivo RAW. Se omitido, usa data/raw/<code>/<code>_<lang>_raw.*",
        )

    def handle(self, *args, **options):
        code = options["code"].strip()
        title = options["title"].strip()
        author_name = options["author"].strip()
        language_code = options["language"].strip().lower()
        seal_slug = options["seal"].strip() or "default"
        publisher = options["publisher"].strip()
        year = options.get("year")
        edition_year = options.get("edition_year")
        raw_path = options.get("raw_path", "").strip()

        if not code:
            raise CommandError("code obrigatorio.")
        if not title:
            raise CommandError("title obrigatorio.")
        if not author_name:
            raise CommandError("author obrigatorio.")

        if not raw_path:
            raw_path = _guess_raw_path(code, language_code)

        raw_path_obj = Path(raw_path) if raw_path else None
        if raw_path_obj and not raw_path_obj.exists():
            raise CommandError(f"RAW nao encontrado: {raw_path_obj}")

        with transaction.atomic():
            language = Language.objects.get_or_create(
                code=language_code,
                defaults={
                    "name": language_code,
                    "native_name": language_code,
                },
            )[0]

            seal = Seal.objects.get_or_create(
                slug=seal_slug,
                defaults={"name": seal_slug},
            )[0]

            author = Contributor.objects.get_or_create(
                name=author_name,
                defaults={"role": ContributorRole.AUTHOR},
            )[0]

            work = Work.objects.get_or_create(
                code=code,
                defaults={
                    "title": title,
                    "original_language": language,
                    "author": author,
                    "publisher": publisher,
                    "year": year,
                },
            )[0]

            edition, _ = Edition.objects.get_or_create(
                work=work,
                language=language,
                seal=seal,
                defaults={
                    "publisher": publisher,
                    "edition_year": edition_year,
                    "raw_source_path": str(raw_path_obj) if raw_path_obj else "",
                },
            )

            if raw_path_obj and edition.raw_source_path != str(raw_path_obj):
                edition.raw_source_path = str(raw_path_obj)
                edition.save(update_fields=["raw_source_path", "updated_at"])

            pipeline, _ = EditionPipeline.objects.get_or_create(edition=edition)
            if pipeline.current_stage == PipelineStage.RAW and pipeline.raw_at is None:
                pipeline.raw_at = pipeline.raw_at or edition.created_at
                pipeline.save(update_fields=["raw_at"])

            texts, _ = EditionText.objects.get_or_create(edition=edition)
            if raw_path_obj:
                texts.raw_path = str(raw_path_obj)
                texts.save(update_fields=["raw_path", "updated_at"])

        self.stdout.write(self.style.SUCCESS(f"OK: edition id={edition.id} [{code} {language_code}]"))


def _guess_raw_path(code: str, language: str) -> str:
    base = Path(__file__).resolve().parents[4] / "data" / "raw" / code
    if not base.exists():
        return ""
    candidates = sorted(base.glob(f"{code}_{language}_raw.*"))
    return str(candidates[0]) if candidates else ""
