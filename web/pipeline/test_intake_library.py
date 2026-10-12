import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from editorial.models import Contributor, Edition, Language, Seal, Work
from pipeline.models import IntakeBatch, IntakeItem
from web.writer.models import SourceDocument


class ExistingLibraryTests(TestCase):
    def setUp(self):
        self.language = Language.objects.create(code="en", name="English", native_name="English")
        self.author = Contributor.objects.create(name="Charlotte Brontë")
        self.batch = IntakeBatch.objects.create(
            batch_code="batch_9000", name="Brontë", slug="bronte", source="GOOGLE_DRIVE",
            remote="fixture", drive_source_path="01_INBOX_RAW/Bronte", status="REGISTERED",
        )

    def item(self, code, title, extension=".epub", metadata=None):
        Work.objects.create(code=code, title=title, author=self.author, original_language=self.language)
        return IntakeItem.objects.create(
            batch=self.batch, book_code=code, title=title, extension=extension,
            remote_path=f"01_INBOX_RAW/Bronte/{code}{extension}",
            relative_path=f"references/{title}{extension}", original_name=f"{title}{extension}",
            size_bytes=12, source_language="en", preview_operation="CREATE", status="REGISTERED",
            canonical_path=f"02_IMPORTED_RAW/batch_9000/{code}/source/{title}{extension}",
            metadata=metadata or {},
        )

    def test_intake_opens_titles_directly_and_excludes_references_without_writes(self):
        self.item("book_9000", "Jane Eyre")
        self.item("book_9001", "Wuthering Heights")
        self.item("book_9002", "PDF reference", ".pdf")
        self.item("book_9003", "Research paper", ".txt", {"document_type": "paper"})
        Work.objects.create(code="book_9004", title="Not imported", author=self.author, original_language=self.language)
        before = list(IntakeItem.objects.values())
        works_before = list(Work.objects.values())
        with patch("gaiden.infrastructure.drive_storage.RcloneDriveStorage", side_effect=AssertionError("remote I/O")):
            response = self.client.get(reverse("intake:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Jane Eyre")
        self.assertContains(response, "Wuthering Heights")
        self.assertNotContains(response, "PDF reference")
        self.assertNotContains(response, "Research paper")
        self.assertNotContains(response, "Not imported")
        self.assertEqual(before, list(IntakeItem.objects.values()))
        self.assertEqual(works_before, list(Work.objects.values()))

    def test_writer_exposes_all_registered_formats_and_nested_local_files_without_copy(self):
        self.item("book_9000", "Jane Eyre")
        self.item("book_9001", "Reference", ".pdf")
        self.item("book_9002", "Appendix", ".png")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "papers").mkdir()
            paths = [root / "papers" / "article.pdf", root / "notes.md", root / "cover.png"]
            for path in paths:
                path.write_bytes(b"existing content")
            (root / "unsafe-link").symlink_to(root / "papers", target_is_directory=True)
            before = {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
            with patch.dict(os.environ, {"GAIDEN_WRITER_SOURCE_ROOT": str(root)}):
                response = self.client.get(reverse("writer:library"))
            self.assertEqual(response.status_code, 200)
            for name in ["Jane Eyre.epub", "Reference.pdf", "Appendix.png", "article.pdf", "notes.md", "cover.png"]:
                self.assertContains(response, name)
            self.assertNotContains(response, "unsafe-link")
            self.assertEqual(before, {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in paths})
            self.assertEqual(SourceDocument.objects.count(), 0)
            self.assertEqual(len(list(root.rglob("*"))), 5)

    def test_writer_preserves_registered_items_in_partially_completed_batch(self):
        self.item("book_9000", "Jane Eyre")
        self.batch.status = "FAILED_RETRYABLE"
        self.batch.save()
        response = self.client.get(reverse("writer:library"))
        self.assertContains(response, "Jane Eyre.epub")

    def test_library_handles_unavailable_local_root(self):
        with patch.dict(os.environ, {"GAIDEN_WRITER_SOURCE_ROOT": "/nonexistent-gaiden-test-path"}):
            response = self.client.get(reverse("writer:library"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "indisponível")

    def test_writer_library_has_one_title_and_one_drive_browser(self):
        response = self.client.get(reverse("writer:library"))
        self.assertContains(response, "<title>Pastas importadas e referências · Gaiden</title>", html=True)
        self.assertContains(response, 'id="library-browser"', count=1)

    def test_existing_editions_keep_one_title_and_direct_links_without_intake_batch(self):
        work = Work.objects.create(code="book_9000", title="Villette", author=self.author, original_language=self.language)
        seal = Seal.objects.create(slug="fixture", name="Fixture")
        edition = Edition.objects.create(work=work, language=self.language, seal=seal)
        response = self.client.get(reverse("intake:home"))
        self.assertContains(response, "Villette", count=1)
        self.assertContains(response, reverse("post_intake_workflow", args=[edition.pk]))
        self.assertEqual(Work.objects.get(pk=work.pk).title, "Villette")

    def test_prior_import_remains_visible_when_a_later_attempt_conflicts(self):
        item = self.item("book_9000", "Shirley")
        item.status = "CONFLICT"
        item.save()
        self.assertContains(self.client.get(reverse("intake:home")), "Shirley")
        self.assertContains(self.client.get(reverse("writer:library")), "Shirley.epub")

    def test_writer_drive_browser_lists_all_formats_without_transfer(self):
        import json
        payload = [
            {"Name": "Scientific Articles", "Path": "Scientific Articles", "IsDir": True},
            {"Name": "paper.pdf", "Path": "paper.pdf", "IsDir": False, "ID": "fixture-file"},
            {"Name": "complete.epub", "Path": "complete.epub", "IsDir": False},
            {"Name": "notes.txt", "Path": "notes.txt", "IsDir": False},
        ]
        with patch("gaiden.infrastructure.drive_storage.RcloneDriveStorage._run", return_value=json.dumps(payload).encode()) as run:
            response = self.client.get(reverse("writer:library_browse"), {"folder": "01_INBOX_RAW/Autoajuda"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["entries"]), 4)
        self.assertEqual(response.json()["entries"][0]["path"], "01_INBOX_RAW/Autoajuda/Scientific Articles")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0], "lsjson")
        self.assertEqual(SourceDocument.objects.count(), 0)

    def test_writer_drive_browser_rejects_paths_outside_source_storage(self):
        with patch("gaiden.infrastructure.drive_storage.RcloneDriveStorage._run") as run:
            for folder in ["../outside", "03_EXPORTS", "01_INBOX_RAW/../03_EXPORTS"]:
                response = self.client.get(reverse("writer:library_browse"), {"folder": folder})
                self.assertEqual(response.status_code, 400)
        run.assert_not_called()

    def test_writer_drive_failure_is_visible_without_retry_or_transfer(self):
        from gaiden.infrastructure.drive_storage import DriveStorageError
        with patch("gaiden.infrastructure.drive_storage.RcloneDriveStorage._run", side_effect=DriveStorageError("fixture failure")) as run:
            response = self.client.get(reverse("writer:library_browse"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("fixture failure", response.content.decode())

    def test_explicit_auxiliary_classifications_are_writer_only(self):
        for index, kind in enumerate(["chapter", "excerpt", "notes", "bibliography", "project_auxiliary"]):
            self.item(f"book_{9100 + index}", f"Auxiliary {index}", ".txt", {"kind": kind})
        intake = self.client.get(reverse("intake:home"))
        library = self.client.get(reverse("writer:library"))
        for index in range(5):
            self.assertNotContains(intake, f"Auxiliary {index}")
            self.assertContains(library, f"Auxiliary {index}.txt")

    def test_pdf_only_legacy_edition_is_not_an_intake_title(self):
        work = Work.objects.create(code="book_9000", title="Legacy PDF", author=self.author, original_language=self.language)
        seal = Seal.objects.create(slug="fixture", name="Fixture")
        Edition.objects.create(work=work, language=self.language, seal=seal, raw_source_path="data/raw/reference.pdf")
        self.assertNotContains(self.client.get(reverse("intake:home")), "Legacy PDF")
        self.assertEqual(Work.objects.get(pk=work.pk).title, "Legacy PDF")
        self.assertContains(self.client.get(reverse("writer:library")), "data/raw/reference.pdf")
