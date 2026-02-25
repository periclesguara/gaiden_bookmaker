from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase

from editorial.models import Edition, EditionPipeline, Work


class RehydrateLibraryCommandTests(TestCase):
    def test_rehydrate_creates_work_edition_and_pipeline(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            books_root = root / "books"
            builds_root = root / "builds"
            frontmatter_root = root / "frontmatter_store"
            raw_root = root / "raw"

            (books_root / "book_0999" / "en").mkdir(parents=True, exist_ok=True)
            (books_root / "book_0999" / "en" / "book_0999_refine_clean.md").write_text(
                "## Sample Chronicle\nBy Jane Writer\n",
                encoding="utf-8",
            )
            (frontmatter_root / "book_0999" / "en").mkdir(parents=True, exist_ok=True)
            (frontmatter_root / "book_0999" / "en" / "copyright.md").write_text(
                "Sample Chronicle: Final Edition\n"
                "Originally published in 1930 by Jane Writer.\n"
                "This edition is an imprint of RinoBooks.\n",
                encoding="utf-8",
            )
            (raw_root / "book_0999" / "en").mkdir(parents=True, exist_ok=True)
            (raw_root / "book_0999" / "en" / "source.txt").write_text(
                "raw source",
                encoding="utf-8",
            )

            call_command(
                "rehydrate_library",
                books_root=str(books_root),
                builds_root=str(builds_root),
                frontmatter_root=str(frontmatter_root),
                extra_roots=str(raw_root),
                langs="en",
                only="book_0999",
            )

            work = Work.objects.get(code="book_0999")
            edition = Edition.objects.get(book_id="book_0999", lang="en")
            pipeline = EditionPipeline.objects.get(edition=edition)

            self.assertEqual(work.title, "Sample Chronicle: Final Edition")
            self.assertEqual(work.author.name, "Jane Writer")
            self.assertEqual(edition.title, "Sample Chronicle: Final Edition")
            self.assertEqual(edition.author, "Jane Writer")
            self.assertEqual(edition.status, Edition.STATUS_REGISTERED)
            self.assertEqual(pipeline.current_stage, Edition.STATUS_REGISTERED)

    def test_rehydrate_dry_run_does_not_write_rows(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            books_root = root / "books"
            frontmatter_root = root / "frontmatter_store"
            (books_root / "book_0998" / "en").mkdir(parents=True, exist_ok=True)
            (frontmatter_root / "book_0998" / "en").mkdir(parents=True, exist_ok=True)

            call_command(
                "rehydrate_library",
                books_root=str(books_root),
                frontmatter_root=str(frontmatter_root),
                builds_root=str(root / "builds"),
                extra_roots=str(root / "raw"),
                langs="en",
                only="book_0998",
                dry_run=True,
            )

            self.assertFalse(Work.objects.filter(code="book_0998").exists())
