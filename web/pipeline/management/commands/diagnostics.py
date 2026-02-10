from __future__ import annotations

import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


def _ensure_project_root_on_path() -> None:
    root = Path(__file__).resolve().parents[4]
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


class Command(BaseCommand):
    help = "Executa checagens de diagnostico do pipeline."

    def add_arguments(self, parser):
        parser.add_argument("--book", type=str, action="append", default=None)
        parser.add_argument("--only-book", type=str, default=None)
        parser.add_argument("--ignore-books", type=str, default=None)
        parser.add_argument("--check", action="append", default=None)
        parser.add_argument("--strict", action="store_true", default=False)
        parser.add_argument("--langs", type=str, default=None)

    def handle(self, *args, **options):
        _ensure_project_root_on_path()
        from gaiden import diagnostics

        checks = options.get("check") or ["all"]
        books = options.get("book")
        only_book = options.get("only_book")
        ignore_books = options.get("ignore_books")
        langs = options.get("langs")
        langs_list = [item for item in langs.split(",") if item] if langs else None
        if only_book and books:
            raise CommandError("Use --only-book or --book (not both).")
        if only_book:
            books = [only_book]
        exit_code = diagnostics.run_checks(
            books,
            checks,
            strict=options.get("strict", False),
            langs=langs_list,
            ignore_books=diagnostics._parse_ignore_books(ignore_books),
        )
        if exit_code != 0:
            raise CommandError("Diagnostics failed.")
