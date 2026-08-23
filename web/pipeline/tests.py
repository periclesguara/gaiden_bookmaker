from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from editorial.models import Contributor, Edition, Language, Seal, Work

from .models import BookEditionTemplate, PipelineJob, PipelineRun, PipelineRunItem
from .services import paths, text_source


class PipelineModelTests(TestCase):
    def test_job_defaults_to_pending_and_has_readable_label(self):
        job = PipelineJob.objects.create(
            book_code="book_0042",
            book_title="The Phoenix on the Sword",
            language="en",
            stage="translate",
        )
        self.assertEqual(job.status, "PENDING")
        self.assertIn("book_0042", str(job))
        self.assertIn("translate", str(job))

    def test_book_template_write_populates_preserved_fields(self):
        template = BookEditionTemplate.objects.create(
            book_code="book_0042",
            language="en",
            title="The Phoenix on the Sword",
            author_name="Robert E. Howard",
            publication_year=2026,
        )
        self.assertIsNone(template.edition_year)
        self.assertEqual(template.edition_copyright_holder, "")
        self.assertEqual(template.editorial_name, "")

    def test_run_item_is_deleted_with_its_run(self):
        run = PipelineRun.objects.create(action="BUILD")
        PipelineRunItem.objects.create(
            run=run,
            book_id=42,
            lang="en",
        )
        self.assertEqual(run.items.count(), 1)
        run.delete()
        self.assertEqual(PipelineRunItem.objects.count(), 0)

    def test_run_item_builds_fallback_book_code(self):
        run = PipelineRun.objects.create(action="NORMALIZE")
        item = PipelineRunItem.objects.create(run=run, book_id=42, lang="en")
        self.assertIn("book_0042", str(item))
        self.assertIn("PENDING", str(item))


class SourceProvenanceIntakeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        language = Language.objects.create(code="en", name="English", native_name="English")
        author = Contributor.objects.create(name="Jane Austen")
        seal = Seal.objects.create(slug="mantaquest", name="MantaQuest")
        work = Work.objects.create(
            code="book_0043",
            title="Editorial Pride and Prejudice",
            original_language=language,
            author=author,
        )
        cls.edition = Edition.objects.create(
            work=work,
            language=language,
            seal=seal,
            title="Editorial Pride and Prejudice",
            author="Jane Austen — editorial",
            language_code="en",
        )

    def test_get_does_not_extract_provenance(self):
        with patch(
            "gaiden.source_provenance.extract_source_provenance_bytes",
            side_effect=AssertionError("GET must not extract"),
        ):
            response = self.client.get(reverse("edition_steps", args=[self.edition.id]))

        self.assertEqual(response.status_code, 200)
        self.edition.work.refresh_from_db()
        self.assertEqual(self.edition.work.source_provenance, {})

    def test_explicit_raw_post_extracts_without_overwriting_editorial_metadata(self):
        raw = (
            b"Title: Pride and Prejudice\nAuthor: Jane Austen\n"
            b"Release Date: June 1, 1998 [EBook #1342]\nLanguage: English\n"
            b"Copyright 1813\n"
        )
        upload = SimpleUploadedFile("pride-source.txt", raw, content_type="text/plain")
        with TemporaryDirectory() as tmp, override_settings(BASE_DIR=Path(tmp) / "web"):
            response = self.client.post(
                reverse("pipeline_run_edition_step", args=[self.edition.id, "raw"]),
                {"raw_file": upload},
            )

        self.assertEqual(response.status_code, 302)
        self.edition.refresh_from_db()
        self.edition.work.refresh_from_db()
        provenance = self.edition.work.source_provenance
        self.assertEqual(provenance["original_title"], "Pride and Prejudice")
        self.assertEqual(provenance["source_filename"], "pride-source.txt")
        self.assertEqual(self.edition.title, "Editorial Pride and Prejudice")
        self.assertEqual(self.edition.author, "Jane Austen — editorial")

    def test_failed_extraction_keeps_filename_hash_and_review_warning(self):
        raw = b"not an epub"
        upload = SimpleUploadedFile("broken.epub", raw, content_type="application/epub+zip")
        with TemporaryDirectory() as tmp, override_settings(BASE_DIR=Path(tmp) / "web"):
            response = self.client.post(
                reverse("pipeline_run_edition_step", args=[self.edition.id, "raw"]),
                {"raw_file": upload},
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.edition.work.refresh_from_db()
        provenance = self.edition.work.source_provenance
        self.assertEqual(provenance["source_filename"], "broken.epub")
        self.assertEqual(len(provenance["source_sha256"]), 64)
        self.assertTrue(provenance["extraction_warnings"])
        self.assertContains(response, "revise o Registro da fonte original")


class PipelinePathTests(SimpleTestCase):
    def _edition(self):
        return SimpleNamespace(
            id=42,
            work=SimpleNamespace(code="book_0042"),
            language=SimpleNamespace(code="en"),
            text_source_mode="auto",
        )

    def test_build_directory_is_scoped_by_book_and_language(self):
        with TemporaryDirectory() as tmp:
            with override_settings(BASE_DIR=Path(tmp) / "web"):
                expected = Path(tmp) / "data" / "builds" / "book_0042" / "en"
                self.assertEqual(paths.edition_build_dir(self._edition()), expected)

    def test_default_merge_priority_prefers_polish(self):
        with TemporaryDirectory() as tmp:
            with override_settings(BASE_DIR=Path(tmp) / "web"):
                self.assertEqual(
                    paths.merge_priority_names(self._edition()),
                    ["merge_polish.txt", "merge_refine.txt", "merge_translate.txt"],
                )

    def test_force_marker_prefers_translation(self):
        with TemporaryDirectory() as tmp:
            with override_settings(BASE_DIR=Path(tmp) / "web"):
                build_dir = paths.edition_build_dir(self._edition())
                build_dir.mkdir(parents=True)
                (build_dir / paths.FORCE_MERGE_TRANSLATE_MARKER).touch()
                self.assertEqual(
                    paths.merge_priority_names(self._edition())[0],
                    "merge_translate.txt",
                )

    def test_legacy_merge_is_copied_to_canonical_name(self):
        with TemporaryDirectory() as tmp:
            with override_settings(BASE_DIR=Path(tmp) / "web"):
                edition = self._edition()
                build_dir = paths.edition_build_dir(edition)
                build_dir.mkdir(parents=True)
                (build_dir / "MERGE_REFINE.TXT").write_text("conteúdo", encoding="utf-8")
                paths.sync_legacy_merges(edition)
                canonical = build_dir / "merge_refine.txt"
                self.assertTrue(canonical.exists())
                self.assertEqual(canonical.read_text(encoding="utf-8"), "conteúdo")

    def test_auto_text_source_selects_highest_priority_existing_file(self):
        with TemporaryDirectory() as tmp:
            with override_settings(BASE_DIR=Path(tmp) / "web"):
                edition = self._edition()
                build_dir = paths.edition_build_dir(edition)
                build_dir.mkdir(parents=True)
                (build_dir / "merge_translate.txt").write_text("translate", encoding="utf-8")
                (build_dir / "merge_polish.txt").write_text("polish", encoding="utf-8")
                info = text_source.get_effective_text_source(edition)
                self.assertEqual(info.canonical_name, "merge_polish.txt")
                self.assertEqual(info.canonical_path.read_text(encoding="utf-8"), "polish")

    def test_missing_manual_source_falls_back_to_auto(self):
        with TemporaryDirectory() as tmp:
            with override_settings(BASE_DIR=Path(tmp) / "web"):
                edition = self._edition()
                edition.text_source_mode = "en::missing.txt"
                build_dir = paths.edition_build_dir(edition)
                build_dir.mkdir(parents=True)
                (build_dir / "merge_refine.txt").write_text("refine", encoding="utf-8")
                info = text_source.get_effective_text_source(edition)
                self.assertEqual(info.canonical_name, "merge_refine.txt")
