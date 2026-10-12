from importlib import import_module

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class CorrectWorkTitlesMigrationTests(TransactionTestCase):
    migrate_from = ("author_studio", "0002_worksplit_workchunk")
    migrate_to = ("author_studio", "0004_chunk_processing_stabilization")

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps
        Author = old_apps.get_model("author_studio", "Author")
        Work = old_apps.get_model("author_studio", "Work")
        WorkSource = old_apps.get_model("author_studio", "WorkSource")
        CanonicalText = old_apps.get_model("author_studio", "CanonicalText")
        WorkChunk = old_apps.get_model("author_studio", "WorkChunk")

        author = Author.objects.create(
            name="Arthur Conan Doyle",
            canonical_name="arthur conan doyle",
            slug="arthur-conan-doyle",
            code="ACD",
        )
        self.expected = {
            "ACD-HOUND": (
                "The Hound of Baskervilles",
                "The Hound of the Baskervilles",
                "the hound of the baskervilles",
                "the-hound-of-the-baskervilles",
            ),
            "ACD-SHER7": (
                "Sherlock Holmes: The Casebook of Sherlock of Sherlock Holmes",
                "The Case-Book of Sherlock Holmes",
                "the case book of sherlock holmes",
                "the-case-book-of-sherlock-holmes",
            ),
        }
        self.related_ids = {}
        for code, (old_title, _, _, _) in self.expected.items():
            work = Work.objects.create(
                author=author,
                title=old_title,
                canonical_title=old_title.lower(),
                slug=old_title.lower().replace(" ", "-"),
                code=code,
            )
            source = WorkSource.objects.create(
                work=work,
                code=f"{code}-SRC001",
                original_filename="source.txt",
                stored_file=f"sources/{code}.txt",
                extension=".txt",
                sha256="a" * 64,
            )
            canonical = CanonicalText.objects.create(
                work=work,
                source=source,
                code=f"{code}-CAN001",
                text_file=f"canonical/{code}.txt",
                sha256="b" * 64,
            )
            chunk = WorkChunk.objects.create(
                work=work,
                canonical_text=canonical,
                code=f"{code}-CHK0001",
                sequence=1,
                unit_type="unknown",
                unit_title="FULL TEXT",
                text_file=f"chunks/{code}.txt",
                sha256="c" * 64,
            )
            self.related_ids[code] = (author.pk, source.pk, canonical.pk, chunk.pk)

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])
        self.apps = self.executor.loader.project_state([self.migrate_to]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_forward_corrects_titles_without_replacing_related_records(self):
        Work = self.apps.get_model("author_studio", "Work")
        WorkSource = self.apps.get_model("author_studio", "WorkSource")
        CanonicalText = self.apps.get_model("author_studio", "CanonicalText")
        WorkChunk = self.apps.get_model("author_studio", "WorkChunk")
        for code, (_, title, canonical_title, slug) in self.expected.items():
            work = Work.objects.get(code=code)
            author_id, source_id, canonical_id, chunk_id = self.related_ids[code]
            self.assertEqual((work.title, work.canonical_title, work.slug), (title, canonical_title, slug))
            self.assertEqual(work.author_id, author_id)
            self.assertTrue(WorkSource.objects.filter(pk=source_id, work=work).exists())
            self.assertTrue(CanonicalText.objects.filter(pk=canonical_id, work=work).exists())
            self.assertTrue(WorkChunk.objects.filter(pk=chunk_id, work=work).exists())

    def test_forward_is_idempotent(self):
        migration = import_module("author_studio.migrations.0003_correct_acd_work_titles")
        before = list(
            self.apps.get_model("author_studio", "Work")
            .objects.filter(code__in=self.expected)
            .order_by("code")
            .values_list("code", "title", "canonical_title", "slug")
        )
        migration.correct_titles(self.apps, None)
        after = list(
            self.apps.get_model("author_studio", "Work")
            .objects.filter(code__in=self.expected)
            .order_by("code")
            .values_list("code", "title", "canonical_title", "slug")
        )
        self.assertEqual(before, after)
