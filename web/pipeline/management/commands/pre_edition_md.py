from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from editorial.models import Edition as EditorialEdition
from pipeline.services import paths
from pipeline.services.md_transform import PreEditionConfig, pre_edition_txt_to_md


class Command(BaseCommand):
    help = "Gera um Markdown de pre-edicao a partir do merge final."

    def add_arguments(self, parser):
        parser.add_argument("--book-code", required=True)
        parser.add_argument("--language", required=True)
        parser.add_argument("--txt-path")
        parser.add_argument("--md-path")
        parser.add_argument("--title")
        parser.add_argument("--subtitle")

    def handle(self, *args, **options):
        book_code = options["book_code"]
        language = options["language"]
        edition = EditorialEdition.objects.filter(
            work__code=book_code,
            language__code=language,
        ).first()
        if not edition:
            raise CommandError(f"Edition not found: {book_code} [{language}]")

        txt_path_opt = options.get("txt_path")
        if txt_path_opt:
            txt_path = Path(txt_path_opt)
        else:
            txt_path = paths.final_merge_txt_path(edition)
            if txt_path is None:
                raise CommandError("No merge_* file found for this edition.")

        if not txt_path.exists():
            raise CommandError(f"TXT not found: {txt_path}")

        md_path_opt = options.get("md_path")
        if md_path_opt:
            md_path = Path(md_path_opt)
        else:
            md_path = paths.edition_build_dir(edition) / "BOOK.PRE_EDITION.md"

        cfg = PreEditionConfig(
            title=options.get("title") or edition.work.title,
            subtitle=options.get("subtitle") or "",
            language=language,
        )
        pre_edition_txt_to_md(txt_path, md_path, cfg)
        self.stdout.write(str(md_path))
