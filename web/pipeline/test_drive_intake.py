from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from editorial.models import Contributor, Edition, EditionPipeline, EditionText, Language, Work
from gaiden.application.intake.drive_import.metadata import filename_metadata, normalize_language, resolve_book_code, text_header_metadata
from gaiden.application.intake.drive_import.service import (
    StaleDrivePreview,
    confirm_drive_folder,
    preview_drive_folder,
    retry_drive_batch,
)
from gaiden.infrastructure.drive_storage import DrivePathError, safe_drive_path
from pipeline.models import (
    BookEditionTemplate,
    IntakeAuditEvent,
    IntakeBatch,
    IntakeItem,
    ManualTranslationJob,
    ProductionBookmark,
)
from pipeline.services import manual_translation


class FakeDriveStorage:
    remote = "fake_drive"
    inbox = "01_INBOX_RAW"
    imported = "02_IMPORTED_RAW"

    def __init__(self, files: dict[str, bytes]):
        self.files = dict(files)
        self.promoted: dict[str, bytes] = {}
        self.download_count = 0
        self.fail_download = False
        self.fail_promotions = False

    def list_folders(self, folder=""):
        return [{"name": "Fixture", "path": "01_INBOX_RAW/Fixture", "id": "folder-1", "modified_at": "v1"}]

    def discover(self, folder, *, recursive=True):
        source = folder if folder.startswith(self.inbox) else f"{self.inbox}/{folder}"
        rows = []
        for index, (name, data) in enumerate(sorted(self.files.items()), start=1):
            rows.append(
                {
                    "remote_file_id": f"file-{index}",
                    "relative_path": name,
                    "remote_path": f"{source}/{name}",
                    "name": Path(name).name,
                    "size": len(data),
                    "mime_type": "text/plain",
                    "modified_at": "2026-08-01T00:00:00Z",
                    "hashes": {"md5": f"hash-{index}"},
                }
            )
        return source, rows

    def download_to(self, remote_path, destination):
        if self.fail_download:
            raise OSError("download failure")
        relative = remote_path.split("/", 2)[-1]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.files[relative])
        self.download_count += 1

    def download_imported_to(self, canonical_path, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.promoted[canonical_path])
        self.download_count += 1

    def promote_file(self, local_path, canonical_path, expected_sha256):
        if self.fail_promotions:
            raise OSError("promotion failure")
        data = local_path.read_bytes()
        if canonical_path in self.promoted and self.promoted[canonical_path] == data:
            return "NO_OP"
        if canonical_path in self.promoted:
            raise OSError("bytes diferentes")
        self.promoted[canonical_path] = data
        return "CREATE"

    def staging_directory(self):
        return tempfile.TemporaryDirectory(prefix="gaiden-drive-test-")


@override_settings(
    GAIDEN_INTAKE_ALLOWED_EXTENSIONS=(".txt", ".md"),
    GAIDEN_INTAKE_MAX_FILE_SIZE=1024 * 1024,
)
class DriveIntakePreviewTests(TestCase):
    def setUp(self):
        Language.objects.create(code="en", name="English", native_name="English")
        self.storage = FakeDriveStorage(
            {
                "book_0050 — Generic Author — First Work.txt": b"First body\n",
                "nested/book_0051 — Generic Author — Second Work.txt": b"Second body\n",
            }
        )

    def test_safe_path_rejects_escape_absolute_and_controls(self):
        for value in ("../outside", "/absolute", "folder/../outside", "bad\x00name"):
            with self.subTest(value=value), self.assertRaises(DrivePathError):
                safe_drive_path(value)
        self.assertEqual(safe_drive_path("01_INBOX_RAW/folder/file.txt"), "01_INBOX_RAW/folder/file.txt")

    def test_metadata_parser_and_code_precedence_are_generic(self):
        metadata = filename_metadata("book_1234 — Any Author — Any Title.txt")
        self.assertEqual(metadata, {"book_code": "book_1234", "author": "Any Author", "title": "Any Title"})
        code, conflict = resolve_book_code(header_code="book_1234", filename_code="book_9999")
        self.assertEqual(code, "")
        self.assertIn("diferentes", conflict)

        headers = text_header_metadata(b"Title: Header Title\nAuthor: Header Author\nLanguage: English (en-GB)\n")
        self.assertEqual(headers["title"], "Header Title")
        self.assertEqual(headers["author"], "Header Author")
        self.assertEqual(normalize_language(headers["language"], "ptbr"), "en")

    def test_preview_is_read_only_deterministic_and_recursive(self):
        before = (IntakeBatch.objects.count(), IntakeItem.objects.count(), Work.objects.count())
        first = preview_drive_folder(
            self.storage,
            folder="Fixture",
            recursive=True,
            batch_name="Generic batch",
            default_author="",
            source_language="en",
        )
        second = preview_drive_folder(
            self.storage,
            folder="Fixture",
            recursive=True,
            batch_name="Generic batch",
            default_author="",
            source_language="en",
        )
        self.assertEqual([item["book_code"] for item in first["items"]], ["book_0050", "book_0051"])
        self.assertEqual(first["snapshot_sha256"], second["snapshot_sha256"])
        self.assertEqual(first["counts"], {"CREATE": 2, "UPDATE": 0, "NO_OP": 0, "CONFLICT": 0})
        self.assertEqual(self.storage.download_count, 0)
        self.assertEqual(before, (IntakeBatch.objects.count(), IntakeItem.objects.count(), Work.objects.count()))

    @override_settings(GAIDEN_INTAKE_MAX_FILE_SIZE=5)
    def test_file_above_limit_is_conflict(self):
        preview = preview_drive_folder(
            self.storage,
            folder="Fixture",
            recursive=True,
            batch_name="Generic batch",
            default_author="",
            source_language="en",
        )
        self.assertEqual(preview["counts"]["CONFLICT"], 2)
        self.assertFalse(preview["can_confirm"])


@override_settings(
    GAIDEN_INTAKE_ALLOWED_EXTENSIONS=(".txt",),
    GAIDEN_INTAKE_MAX_FILE_SIZE=1024 * 1024,
)
class DriveIntakeConfirmationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        Language.objects.create(code="en", name="English", native_name="English")
        self.storage = FakeDriveStorage(
            {"book_0050 — Generic Author — First Work.txt": b"A confirmed body.\n"}
        )

    def preview(self):
        return preview_drive_folder(
            self.storage,
            folder="Fixture",
            recursive=True,
            batch_name="Generic batch",
            default_author="",
            source_language="en",
        )

    def test_confirmation_and_full_rerun_are_idempotent(self):
        first_preview = self.preview()
        first = confirm_drive_folder(self.storage, first_preview)
        self.assertEqual(first["counts"]["created"], 1)
        self.assertEqual(first["status"], "REGISTERED")
        self.assertEqual(IntakeBatch.objects.count(), 1)
        self.assertEqual(IntakeItem.objects.count(), 1)
        self.assertEqual(Work.objects.filter(code="book_0050").count(), 1)
        self.assertEqual(Work.objects.get(code="book_0050").source_provenance, {})
        self.assertEqual(IntakeItem.objects.get().status, "REGISTERED")
        self.assertTrue(IntakeItem.objects.get().canonical_path.startswith("02_IMPORTED_RAW/"))

        second_preview = self.preview()
        self.assertEqual(second_preview["counts"], {"CREATE": 0, "UPDATE": 0, "NO_OP": 1, "CONFLICT": 0})
        second = confirm_drive_folder(self.storage, second_preview)
        self.assertEqual(second["counts"]["noop"], 1)
        self.assertEqual(IntakeBatch.objects.count(), 1)
        self.assertEqual(IntakeItem.objects.count(), 1)
        self.assertEqual(Work.objects.filter(code="book_0050").count(), 1)
        self.assertGreaterEqual(IntakeAuditEvent.objects.count(), 3)

    def test_changed_folder_invalidates_preview(self):
        preview = self.preview()
        self.storage.files["book_0050 — Generic Author — First Work.txt"] = b"Changed after preview.\n"
        with self.assertRaises(StaleDrivePreview):
            confirm_drive_folder(self.storage, preview)
        self.assertEqual(IntakeBatch.objects.count(), 0)

    def test_download_failure_does_not_create_database_rows(self):
        preview = self.preview()
        self.storage.fail_download = True
        with self.assertRaises(OSError):
            confirm_drive_folder(self.storage, preview)
        self.assertEqual(IntakeBatch.objects.count(), 0)
        self.assertEqual(IntakeItem.objects.count(), 0)
        self.assertEqual(Work.objects.count(), 0)

    def test_failed_promotion_retries_same_batch_item_and_code(self):
        preview = self.preview()
        self.storage.fail_promotions = True
        first = confirm_drive_folder(self.storage, preview)
        self.assertEqual(first["status"], "FAILED_RETRYABLE")
        self.assertEqual(first["counts"]["failed"], 1)
        item_pk = IntakeItem.objects.get().pk
        work_pk = Work.objects.get(code="book_0050").pk

        self.storage.fail_promotions = False
        retry = retry_drive_batch(self.storage, "batch_0001")
        self.assertEqual(retry["status"], "REGISTERED")
        self.assertEqual(retry["retried"], ["book_0050"])
        self.assertEqual(IntakeBatch.objects.count(), 1)
        self.assertEqual(IntakeItem.objects.get().pk, item_pk)
        self.assertEqual(Work.objects.get(code="book_0050").pk, work_pk)

    def test_partial_selection_imports_only_selected_item(self):
        self.storage.files["book_0051 — Generic Author — Second Work.txt"] = b"Second body.\n"
        preview = self.preview()
        result = confirm_drive_folder(
            self.storage,
            preview,
            selected_paths=["book_0051 — Generic Author — Second Work.txt"],
        )
        self.assertEqual(result["counts"]["created"], 1)
        self.assertEqual(list(IntakeItem.objects.values_list("book_code", flat=True)), ["book_0051"])

    def test_empty_selection_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "Seleção de arquivos inválida"):
            confirm_drive_folder(self.storage, self.preview(), selected_paths=[])
        self.assertEqual(IntakeBatch.objects.count(), 0)


@override_settings(
    GAIDEN_INTAKE_ALLOWED_EXTENSIONS=(".txt",),
    GAIDEN_INTAKE_MAX_FILE_SIZE=1024 * 1024,
)
class DriveIntakeInterfaceTests(TestCase):
    def setUp(self):
        Language.objects.create(code="en", name="English", native_name="English")
        self.storage = FakeDriveStorage(
            {"book_0050 — Generic Author — First Work.txt": b"First body\n"}
        )
        self.client = Client()

    @patch("pipeline.views_incremental.RcloneDriveStorage")
    def test_dashboard_browse_and_preview_are_read_only(self, storage_class):
        storage_class.return_value = self.storage
        dashboard = self.client.get(reverse("automated_editorial_import"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "Importar pasta existente do Google Drive")
        browse = self.client.get(reverse("automated_drive_browse"))
        self.assertEqual(browse.status_code, 200)
        preview = self.client.post(
            reverse("automated_drive_folder_preview"),
            {
                "folder_path": "Fixture",
                "batch_name": "Generic batch",
                "default_author": "",
                "source_language": "en",
                "target_language": "",
                "seal": "",
                "recursive": "on",
            },
        )
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "book_0050")
        self.assertEqual(self.storage.download_count, 0)
        self.assertEqual(IntakeBatch.objects.count(), 0)

    @patch("pipeline.views_incremental.RcloneDriveStorage")
    def test_confirmation_requires_post(self, storage_class):
        storage_class.return_value = self.storage
        response = self.client.get(reverse("automated_drive_folder_confirm"))
        self.assertEqual(response.status_code, 302)

    @patch("pipeline.views_incremental.RcloneDriveStorage")
    def test_confirmation_is_csrf_protected(self, storage_class):
        storage_class.return_value = self.storage
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(
            reverse("automated_drive_folder_confirm"),
            {"preview_token": "invalid", "selected_paths": "file.txt"},
        )
        self.assertEqual(response.status_code, 403)


@override_settings(
    GAIDEN_INTAKE_ALLOWED_EXTENSIONS=(".txt",),
    GAIDEN_INTAKE_MAX_FILE_SIZE=1024 * 1024,
)
class ImportedBookProductionTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="gaiden-production-test-")
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.payload = b"Title: Example\nAuthor: Generic Author\n\nBody\n"
        language = Language.objects.create(code="en", name="English", native_name="English")
        author = Contributor.objects.create(name="Generic Author", role="AUTHOR")
        Work.objects.create(
            code="book_0056",
            title="Example",
            original_language=language,
            author=author,
        )
        batch = IntakeBatch.objects.create(
            batch_code="batch_0001",
            name="Generic batch",
            slug="generic-batch",
            source="GOOGLE_DRIVE",
            remote="fake_drive",
            drive_source_path="01_INBOX_RAW/Generic",
            status="REGISTERED",
        )
        self.item = IntakeItem.objects.create(
            batch=batch,
            remote_file_id="file-56",
            remote_path="01_INBOX_RAW/Generic/book_0056.txt",
            relative_path="book_0056.txt",
            original_name="book_0056.txt",
            size_bytes=len(self.payload),
            mime_type="text/plain",
            extension=".txt",
            remote_version="v1",
            sha256=__import__("hashlib").sha256(self.payload).hexdigest(),
            title="Example",
            author_name="Generic Author",
            source_language="en",
            book_code="book_0056",
            preview_operation="CREATE",
            status="REGISTERED",
            canonical_path="02_IMPORTED_RAW/batch_0001__generic/book_0056/source/book_0056.txt",
        )

    def _extract_result(self, book_code, language, staged_path):
        original = self.root / "raw" / "original.txt"
        canonical_txt = self.root / "raw" / "canonical.txt"
        canonical_html = self.root / "raw" / "canonical.html"
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(Path(staged_path).read_bytes())
        canonical_txt.write_bytes(Path(staged_path).read_bytes())
        canonical_html.write_text("", encoding="utf-8")
        return {
            "input_format": "txt",
            "original_file": str(original),
            "canonical_txt": str(canonical_txt),
            "canonical_html": str(canonical_html),
            "meta_file": str(self.root / "raw" / "meta.json"),
        }

    def test_list_shows_preview_and_explicit_editor_selection_actions(self):
        response = self.client.get(reverse("imported_book_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selecionar um livro importado")
        self.assertContains(response, "book_0056")
        self.assertContains(response, "book_0056.txt")
        self.assertContains(response, ">Prévia de leitura<")
        self.assertContains(response, ">Enviar ao bloco de edição<")

    @patch("pipeline.views.RcloneDriveStorage")
    def test_reading_preview_downloads_verified_source_without_creating_editorial_records(self, storage_class):
        fake_storage = FakeDriveStorage({})
        fake_storage.promoted[self.item.canonical_path] = self.payload
        storage_class.return_value = fake_storage
        counts_before = (
            Edition.objects.count(),
            BookEditionTemplate.objects.count(),
            IntakeAuditEvent.objects.count(),
        )
        metadata_before = dict(self.item.metadata or {})

        response = self.client.get(reverse("imported_book_preview", kwargs={"item_id": self.item.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Prévia de leitura")
        self.assertContains(response, "Title: Example")
        self.assertContains(response, "Enviar este arquivo ao bloco de edição")
        self.assertEqual(fake_storage.download_count, 1)
        self.item.refresh_from_db()
        self.assertEqual(dict(self.item.metadata or {}), metadata_before)
        self.assertEqual(
            (
                Edition.objects.count(),
                BookEditionTemplate.objects.count(),
                IntakeAuditEvent.objects.count(),
            ),
            counts_before,
        )

    @patch("pipeline.views.RcloneDriveStorage")
    def test_reading_preview_rejects_a_source_with_a_different_sha256(self, storage_class):
        fake_storage = FakeDriveStorage({})
        fake_storage.promoted[self.item.canonical_path] = b"different bytes"
        storage_class.return_value = fake_storage

        response = self.client.get(
            reverse("imported_book_preview", kwargs={"item_id": self.item.id}),
            follow=True,
        )

        self.assertRedirects(response, reverse("imported_book_list"))
        self.assertContains(response, "SHA-256 do arquivo importado diverge")
        self.assertFalse(Edition.objects.exists())
        self.assertFalse(BookEditionTemplate.objects.exists())
        self.assertFalse(IntakeAuditEvent.objects.exists())

    @patch("pipeline.views.kdp_mode.build_frontmatter_files")
    @patch("pipeline.views.pipeline_ingest.run_source_extract")
    @patch("pipeline.views.RcloneDriveStorage")
    def test_edit_selects_source_and_opens_existing_pipeline_idempotently(
        self,
        storage_class,
        source_extract,
        build_frontmatter,
    ):
        fake_storage = FakeDriveStorage({})
        fake_storage.promoted[self.item.canonical_path] = self.payload
        storage_class.return_value = fake_storage
        source_extract.side_effect = self._extract_result

        url = reverse("imported_book_select", kwargs={"item_id": self.item.id})
        response = self.client.post(url)
        edition = Edition.objects.get(work__code="book_0056", language__code="en")
        production_url = reverse("post_intake_workflow", kwargs={"edition_id": edition.id})
        self.assertRedirects(response, production_url)
        template = BookEditionTemplate.objects.get(book_code="book_0056", language="en")
        self.assertEqual(template.registration_status, BookEditionTemplate.STATUS_READY_FOR_BLOCK_02)
        self.assertEqual(template.source_file_sha256, self.item.sha256)
        self.assertEqual(template.source_uploaded_by, "Automated Intake")
        self.assertEqual(EditionText.objects.get(edition=edition).raw_path, str(self.root / "raw" / "canonical.txt"))
        self.assertEqual(EditionPipeline.objects.get(edition=edition).current_stage, "SOURCE_EXTRACTED")
        self.item.refresh_from_db()
        self.assertEqual(self.item.metadata["production"]["edition_id"], edition.id)
        self.assertTrue(
            IntakeAuditEvent.objects.filter(item=self.item, operation="SELECT_FOR_PRODUCTION").exists()
        )

        second = self.client.post(url)
        self.assertRedirects(second, production_url)
        self.assertEqual(fake_storage.download_count, 1)
        self.assertEqual(BookEditionTemplate.objects.filter(book_code="book_0056", language="en").count(), 1)
        self.assertEqual(Edition.objects.filter(work__code="book_0056", language__code="en").count(), 1)
        build_frontmatter.assert_called_once()

    def test_selection_requires_post(self):
        response = self.client.get(reverse("imported_book_select", kwargs={"item_id": self.item.id}))
        self.assertRedirects(response, reverse("imported_book_list"))

    @patch("pipeline.views.kdp_mode.build_frontmatter_files")
    @patch("pipeline.views.pipeline_ingest.run_source_extract")
    @patch("pipeline.views.RcloneDriveStorage")
    def test_post_intake_page_replaces_legacy_pipeline_after_selection(
        self,
        storage_class,
        source_extract,
        build_frontmatter,
    ):
        fake_storage = FakeDriveStorage({})
        fake_storage.promoted[self.item.canonical_path] = self.payload
        storage_class.return_value = fake_storage
        source_extract.side_effect = self._extract_result
        response = self.client.post(reverse("imported_book_select", kwargs={"item_id": self.item.id}))
        page = self.client.get(response.url)
        self.assertContains(page, "Fluxo pós-Intake")
        self.assertContains(page, "01 · Normalize")
        self.assertContains(page, "02 · Headings Cleaner")
        self.assertContains(page, "03 · Google Drive Translate")
        self.assertContains(page, "04 · Importar tradução")
        self.assertContains(page, "04 · Buscar e importar miolo do Drive")
        self.assertContains(page, "04 · Importar miolo selecionado")
        self.assertNotContains(page, "Bloco 02 · Core do Sistema")

    @patch("pipeline.views.kdp_mode.build_frontmatter_files")
    @patch("pipeline.views.pipeline_ingest.run_source_extract")
    @patch("pipeline.views.RcloneDriveStorage")
    def test_save_current_work_and_show_it_on_editions_dashboard(
        self,
        storage_class,
        source_extract,
        build_frontmatter,
    ):
        fake_storage = FakeDriveStorage({})
        fake_storage.promoted[self.item.canonical_path] = self.payload
        storage_class.return_value = fake_storage
        source_extract.side_effect = self._extract_result
        self.client.post(reverse("imported_book_select", kwargs={"item_id": self.item.id}))
        edition = Edition.objects.get(work__code="book_0056", language__code="en")

        response = self.client.post(
            reverse("save_production_bookmark", kwargs={"edition_id": edition.id}),
            {"target_language": "ptbr"},
        )
        self.assertRedirects(response, reverse("production_dashboard"))
        first_bookmark = ProductionBookmark.objects.get()
        self.assertEqual(first_bookmark.edition, edition)
        self.assertEqual(first_bookmark.target_language, "ptbr")
        with self.assertRaises(ValueError):
            first_bookmark.target_language = "fr"
            first_bookmark.save()
        with self.assertRaises(ValueError):
            first_bookmark.delete()

        second = self.client.post(
            reverse("save_production_bookmark", kwargs={"edition_id": edition.id}),
            {"target_language": "en_us"},
        )
        self.assertRedirects(second, reverse("production_dashboard"))
        self.assertEqual(ProductionBookmark.objects.count(), 2)
        first_bookmark.refresh_from_db()
        self.assertEqual(first_bookmark.target_language, "ptbr")
        self.assertEqual(ProductionBookmark.objects.order_by("-saved_at", "-id").first().target_language, "en_us")

        dashboard = self.client.get(reverse("production_dashboard"))
        self.assertContains(dashboard, "Dashboard de edições")
        self.assertContains(dashboard, "Em trabalho agora")
        self.assertContains(dashboard, "book_0056")
        self.assertContains(dashboard, "Histórico permanente de retomadas")
        self.assertContains(dashboard, "Edições concluídas · DONE")
        self.assertContains(dashboard, "Códigos reservados pelo Intake")

    @patch("pipeline.views.manual_translation.export_job")
    def test_google_drive_export_registers_book_subfolder(self, export_job):
        template = BookEditionTemplate.objects.create(
            book_code="book_0056",
            language="en",
            title="Example",
            author_name="Generic Author",
            publication_year=2026,
            source_saved_path=str(self.root / "source.txt"),
        )
        (self.root / "source.txt").write_bytes(self.payload)
        from pipeline.views import _save_template_and_edition_metadata
        edition, _ = _save_template_and_edition_metadata(template)
        export_job.return_value = {
            "drive_path": "gaiden_drive:04_TRANSLATION_JOBS/book_0056/en-us",
            "source_path": str(self.root / "clean.txt"),
            "source_sha256": "a" * 64,
            "expected_return_name": "book_0056_en_us_translated.txt",
        }
        response = self.client.post(
            reverse("manual_translation_export", kwargs={"edition_id": edition.id}),
            {"target_language": "en_us"},
        )
        self.assertEqual(response.status_code, 302)
        job = ManualTranslationJob.objects.get(edition=edition, target_language="en_us")
        self.assertEqual(job.drive_path, "gaiden_drive:04_TRANSLATION_JOBS/book_0056/en-us")
        self.assertEqual(job.status, ManualTranslationJob.STATUS_EXPORTED)
        page = self.client.get(response.url)
        self.assertContains(page, "Import Google Drive")
        self.assertContains(page, "Upload do computador")

        export_job.reset_mock()
        repeated = self.client.post(
            reverse("manual_translation_export", kwargs={"edition_id": edition.id}),
            {"target_language": "en_us"},
        )
        self.assertEqual(repeated.status_code, 302)
        export_job.assert_not_called()

        confirmed = self.client.post(
            reverse("manual_translation_export", kwargs={"edition_id": edition.id}),
            {"target_language": "en_us", "confirm_replace": "1"},
        )
        self.assertEqual(confirmed.status_code, 302)
        export_job.assert_called_once()

    def test_manual_translation_service_publishes_input_contract_and_return_marker(self):
        source = self.root / "clean.txt"
        source.write_bytes(self.payload)

        class CapturePublisher:
            def __init__(self):
                self.files = {}

            def publish_bytes(self, relative_path, data):
                self.files[relative_path] = data

        publisher = CapturePublisher()
        result = manual_translation.export_job(
            book_code="book_0056",
            title="Example",
            author="Generic Author",
            source_language="en",
            target_language="en_us",
            source_path=source,
            publisher=publisher,
        )
        self.assertEqual(result["drive_path"], "gaiden_drive:04_TRANSLATION_JOBS/book_0056/en-us")
        self.assertIn("input/book_0056_heading_clean.txt", publisher.files)
        self.assertIn("input/translation-job.json", publisher.files)
        self.assertIn("return/RETURN_HERE.txt", publisher.files)

    @patch("pipeline.views.paths.merge_translate_path")
    @patch("pipeline.views._agent_translate_out_dir")
    def test_local_translation_upload_promotes_return_for_block_03(self, out_dir, merge_path):
        template = BookEditionTemplate.objects.create(
            book_code="book_0056",
            language="en",
            title="Example",
            author_name="Generic Author",
            publication_year=2026,
            source_saved_path=str(self.root / "source.txt"),
        )
        (self.root / "source.txt").write_bytes(self.payload)
        from pipeline.views import _save_template_and_edition_metadata
        edition, _ = _save_template_and_edition_metadata(template)
        out_dir.return_value = self.root / "translated" / "en_us"
        merge_path.return_value = self.root / "build" / "merge_translate.txt"
        job = ManualTranslationJob.objects.create(
            edition=edition,
            target_edition=edition,
            source_language="en",
            target_language="en_us",
            drive_path="gaiden_drive:04_TRANSLATION_JOBS/book_0056/en-us",
            source_path=str(self.root / "clean.txt"),
            source_sha256="a" * 64,
            expected_return_name="book_0056_en_us_translated.txt",
        )
        translated = b"Translated complete text\n"
        response = self.client.post(
            reverse("manual_translation_import_upload", kwargs={"job_id": job.id}),
            {"translated_file": SimpleUploadedFile("translated.txt", translated, content_type="text/plain")},
        )
        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.status, ManualTranslationJob.STATUS_IMPORTED)
        self.assertEqual((self.root / "build" / "merge_translate.txt").read_bytes(), translated)
        self.assertEqual((self.root / "translated" / "en_us" / "merge_refine_clean.txt").read_bytes(), translated)
        state = EditionPipeline.objects.get(edition=edition)
        self.assertEqual(state.current_stage, "MERGED")
