import hashlib
import io
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from editorial.models import Contributor, Edition, EditionPipeline, EditionText, Language, PipelineStage, Work
from gaiden.application.intake.drive_sync import discover_drive_folder, download_drive_item
from gaiden.application.intake import drive_sync as drive_sync_service
from gaiden.application.intake import ingestion as ingestion_service
from gaiden.application.intake.ingestion import (
    clean_downloaded_item,
    ingest_bytes,
    store_uploaded_files,
)
from gaiden.application.intake.pipeline_handoff import (
    IntakeHandoffConflict,
    IntakeHandoffError,
    handoff_to_pipeline,
    open_in_bookmaker,
)
from gaiden.application.intake.translation import (
    confirm_ready_for_editing,
    prepare_for_codex,
    register_translation_return,
)
from gaiden.domain.intake import IntakeState
from gaiden.infrastructure import intake_storage, storage
from gaiden.infrastructure.intake_drive import DriveFile
from pipeline.models import BookEditionTemplate
from web.intake_module.models import IntakeBatch, IntakeItem


class FakeConverter:
    def convert_to_markdown(self, source_path):
        return Path(source_path).read_text(encoding="utf-8")


class FakeDiscoveryClient:
    def __init__(self, payload=b"Chapter One\nTarzan begins.\n"):
        self.file = DriveFile(
            "tarzan-id",
            "edgar-rice-burroughs_tarzan-of-the-apes.epub",
            "edgar-rice-burroughs_tarzan-of-the-apes.epub",
            len(payload),
        )
        self.payload = payload
        self.checked = 0
        self.downloaded = 0

    def check_available(self):
        self.checked += 1

    def list_files(self, _relative_path):
        return [self.file]

    def download_file(self, _folder, _drive_file, destination):
        self.downloaded += 1
        destination.write_bytes(self.payload)
        return destination


class FakeSelectionClient:
    remote = "gaiden_test_drive:"
    inbox = "01_INBOX_RAW"
    executable_available = True

    def __init__(self):
        self.files = [
            DriveFile(
                "tarzan-id",
                "edgar-rice-burroughs_tarzan-of-the-apes.epub",
                "edgar-rice-burroughs_tarzan-of-the-apes.epub",
                21,
            ),
            DriveFile("mars-id", "a-princess-of-mars.epub", "a-princess-of-mars.epub", 17),
            DriveFile("cover-id", "cover.jpg", "cover.jpg", 9),
        ]
        self.payloads = {
            "edgar-rice-burroughs_tarzan-of-the-apes.epub": b"Tarzan selected payload",
            "a-princess-of-mars.epub": b"Mars payload",
        }
        self.checked = 0
        self.downloaded = []

    def check_available(self):
        self.checked += 1

    def list_folders(self, _relative_path):
        return ["Edgar_Rice_Burroughs", "Other_Folder"]

    def list_files(self, _relative_path):
        return list(self.files)

    def stored_folder_path(self, folder_name):
        return f"{self.inbox}/{folder_name}"

    def direct_child_name(self, stored_path):
        return stored_path.removeprefix(f"{self.inbox}/")

    def download_file(self, _folder, drive_file, destination):
        self.downloaded.append(drive_file.name)
        destination.write_bytes(self.payloads[drive_file.name])
        return destination


class FakeUpload:
    def __init__(self, name, payload):
        self.name = name
        self.payload = payload

    def chunks(self):
        yield self.payload


def bookmaker_epub_payload() -> bytes:
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
            <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles><rootfile full-path="OPS/content.opf"
                media-type="application/oebps-package+xml"/></rootfiles>
            </container>""",
        )
        archive.writestr(
            "OPS/content.opf",
            """<?xml version="1.0"?>
            <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
              <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                <dc:title>Tarzan of the Apes</dc:title>
                <dc:creator>Edgar Rice Burroughs</dc:creator>
                <dc:language>en</dc:language>
                <dc:identifier>tarzan-apes</dc:identifier>
              </metadata>
              <manifest>
                <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
              </manifest>
              <spine><itemref idref="chapter"/></spine>
            </package>""",
        )
        archive.writestr(
            "OPS/chapter.xhtml",
            "<html><body><h1>Chapter One</h1><p>Tarzan begins.</p></body></html>",
        )
    return stream.getvalue()


class BlockZeroTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="gaiden-intake-block-zero-")
        self.addCleanup(temporary.cleanup)
        self.storage_root = Path(temporary.name) / "data"
        environment = patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.storage_root)}, clear=False)
        environment.start()
        self.addCleanup(environment.stop)
        self.batch = IntakeBatch.objects.create(
            code="erb_pilot",
            name="Edgar Rice Burroughs pilot",
            author_default="Edgar Rice Burroughs",
            source_language="en",
            imprint_default="RinoBooks",
            editor_default="Gaiden Editor",
            collection_name="Tarzan",
            public_domain=True,
            drive_relative_path="Edgar_Rice_Burroughs",
        )

    def _clean_item(self, *, filename="tarzan.txt"):
        result = ingest_bytes(
            self.batch,
            filename,
            b"Chapter One\nTarzan begins.\n",
            converter=FakeConverter(),
        )
        item = result["item"]
        item.confirmed_title = "Tarzan of the Apes"
        item.original_year = 1912
        item.book_code = "book_tarzan_apes"
        item.target_language = "de"
        item.save(
            update_fields=[
                "confirmed_title",
                "original_year",
                "book_code",
                "target_language",
                "updated_at",
            ]
        )
        return item

    def _ready_item(self):
        item = self._clean_item()
        manifest = prepare_for_codex(item, target_language="de")
        register_translation_return(
            item,
            Path(manifest["expected_return_path"]).name,
            "Kapitel Eins\nTarzan beginnt.\n".encode("utf-8"),
        )
        confirm_ready_for_editing(item)
        item.refresh_from_db()
        return item

    def _bookmaker_item(self, *, status=IntakeState.DOWNLOADED.value):
        result = store_uploaded_files(
            self.batch,
            [
                FakeUpload(
                    "edgar-rice-burroughs_tarzan-of-the-apes.epub",
                    bookmaker_epub_payload(),
                )
            ],
        )[0]
        item = result["item"]
        item.confirmed_title = "Tarzan of the Apes"
        item.original_year = 1912
        item.book_code = "book_0031"
        item.target_language = "en-us"
        item.status = status
        item.save(
            update_fields=[
                "confirmed_title",
                "original_year",
                "book_code",
                "target_language",
                "status",
                "updated_at",
            ]
        )
        return item

    def test_dashboard_shows_block_zero_stats_and_current_individual_upload(self):
        IntakeItem.objects.create(
            batch=self.batch,
            order_index=1,
            source_filename="pilot.epub",
            source_format="epub",
            status=IntakeState.DISCOVERED.value,
        )
        response = self.client.get(reverse("root"))
        self.assertContains(response, "Bloco 00 — Cadastro Geral")
        self.assertContains(response, "Lotes: 1")
        self.assertContains(response, "Descobertos: 1")
        self.assertContains(response, "Upload individual — método atual")
        self.assertContains(response, reverse("book_edition_new"))

    def _batch_form_data(self, code):
        return {
            "name": code,
            "author_default": "Edgar Rice Burroughs",
            "source_language": "en",
            "imprint_default": "RinoBooks",
            "editor_default": "Gaiden Editor",
            "collection_name": "",
            "public_domain": "on",
        }

    def test_create_batch_shows_both_save_actions(self):
        response = self.client.get(reverse("intake_module:batch_create"))
        self.assertContains(response, "Salvar lote e adicionar arquivos")
        self.assertContains(response, "Salvar somente o cadastro")
        self.assertContains(response, "Buscar pastas no Google Drive")
        self.assertContains(response, reverse("intake_module:drive_folders"))
        self.assertContains(response, "Selecionar pasta do computador")
        self.assertNotContains(response, 'name="code"')
        self.assertNotContains(response, 'name="drive_relative_path"')

    def test_batch_code_is_generated_and_collision_gets_unique_suffix(self):
        first = IntakeBatch.objects.create(name="Shared Folder", source_language="en")
        second = IntakeBatch.objects.create(name="Shared Folder", source_language="en")
        self.assertEqual(first.code, "shared-folder")
        self.assertEqual(second.code, "shared-folder-2")

    def test_drive_folder_selection_sets_internal_path_code_and_lists_without_download(self):
        client = FakeSelectionClient()
        with patch("web.intake_module.views.RcloneClient", return_value=client):
            list_response = self.client.get(reverse("intake_module:drive_folders"))
            self.assertContains(list_response, "Edgar_Rice_Burroughs")
            self.assertContains(list_response, "Selecionar esta pasta")
            select_response = self.client.post(
                reverse("intake_module:drive_folder_select"),
                {"drive_folder": "Edgar_Rice_Burroughs"},
            )
            self.assertRedirects(
                select_response,
                reverse("intake_module:batch_create"),
                fetch_redirect_response=False,
            )
            metadata_response = self.client.get(reverse("intake_module:batch_create"))
            self.assertContains(metadata_response, 'value="Edgar_Rice_Burroughs"')
            self.assertContains(metadata_response, "Salvar cadastro e listar arquivos")
            response = self.client.post(
                reverse("intake_module:batch_create"),
                {
                    **self._batch_form_data("temporary"),
                    "create_action": "save_drive_folder",
                    "drive_folder": "Edgar_Rice_Burroughs",
                },
            )
        batch = IntakeBatch.objects.get(name="Edgar_Rice_Burroughs")
        self.assertEqual(batch.code, "edgar_rice_burroughs")
        self.assertEqual(batch.drive_relative_path, "01_INBOX_RAW/Edgar_Rice_Burroughs")
        self.assertEqual(response.url, reverse("intake_module:batch_files", args=[batch.id]))
        self.assertEqual(batch.items.count(), 2)
        self.assertFalse(batch.items.exclude(status=IntakeState.DISCOVERED.value).exists())
        self.assertEqual(client.downloaded, [])

    def test_drive_folder_listing_does_not_download_any_file(self):
        client = FakeSelectionClient()
        with patch("web.intake_module.views.RcloneClient", return_value=client):
            with patch("gaiden.infrastructure.intake_drive.subprocess.run") as subprocess_run:
                response = self.client.get(reverse("intake_module:drive_folders"))
        self.assertContains(
            response,
            "<p><strong>Remote:</strong> gaiden_test_drive:01_INBOX_RAW</p>",
            html=True,
        )
        self.assertContains(response, "Edgar_Rice_Burroughs")
        self.assertContains(response, "Selecionar esta pasta")
        self.assertEqual(client.downloaded, [])
        subprocess_run.assert_not_called()

    def test_drive_folder_selection_rejects_nonexistent_folder(self):
        client = FakeSelectionClient()
        with patch("web.intake_module.views.RcloneClient", return_value=client):
            response = self.client.post(
                reverse("intake_module:drive_folder_select"),
                {"drive_folder": "Missing_Folder"},
                follow=True,
            )
        self.assertContains(response, "A pasta selecionada não existe")
        self.assertFalse(IntakeBatch.objects.filter(name="Missing_Folder").exists())

    def test_rclone_error_is_safe_on_drive_folder_page(self):
        client = FakeSelectionClient()
        client.check_available = lambda: (_ for _ in ()).throw(RuntimeError("secret config path"))
        with patch("web.intake_module.views.RcloneClient", return_value=client):
            response = self.client.get(reverse("intake_module:drive_folders"))
        self.assertContains(
            response,
            "Não foi possível consultar o Google Drive. Verifique a configuração do remote gaiden_drive.",
        )
        self.assertNotContains(response, "secret config path")

    def test_local_directory_selection_uses_folder_name_without_local_path(self):
        response = self.client.post(
            reverse("intake_module:batch_create"),
            data={
                **self._batch_form_data("temporary"),
                "create_action": "select_local_folder",
                "local_folder_name": "Tarzan_Local",
                "folder_files": [
                    SimpleUploadedFile("tarzan.txt", b"Tarzan local", content_type="text/plain"),
                    SimpleUploadedFile("notes.html", b"<p>Notes</p>", content_type="text/html"),
                ],
            },
        )
        batch = IntakeBatch.objects.get(name="Tarzan_Local")
        self.assertEqual(response.url, reverse("intake_module:batch_files", args=[batch.id]))
        self.assertEqual(batch.code, "tarzan_local")
        self.assertEqual(batch.drive_relative_path, "")
        self.assertEqual(batch.items.count(), 2)
        self.assertFalse(batch.items.exclude(status=IntakeState.DOWNLOADED.value).exists())

    def test_folder_selection_rejects_path_traversal(self):
        client = FakeSelectionClient()
        with patch("web.intake_module.views.RcloneClient", return_value=client):
            response = self.client.post(
                reverse("intake_module:drive_folder_select"),
                {"drive_folder": "../Edgar_Rice_Burroughs"},
                follow=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "caminhos arbitrários não são aceitos")
        self.assertFalse(IntakeBatch.objects.filter(name="temporary").exists())

        response = self.client.post(
            reverse("intake_module:batch_create"),
            data={
                **self._batch_form_data("local-temporary"),
                "create_action": "select_local_folder",
                "local_folder_name": "/home/user/books",
                "folder_files": [SimpleUploadedFile("book.txt", b"Book", content_type="text/plain")],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "caminhos arbitrários não são aceitos")
        self.assertFalse(IntakeBatch.objects.filter(name="local-temporary").exists())

    def test_create_batch_redirects_to_file_selection_by_default(self):
        response = self.client.post(
            reverse("intake_module:batch_create"),
            {**self._batch_form_data("new_files"), "save_and_files": "1"},
        )
        batch = IntakeBatch.objects.get(code="new_files")
        self.assertRedirects(
            response,
            reverse("intake_module:batch_files", args=[batch.id]),
            fetch_redirect_response=False,
        )
        self.assertEqual(response.url, f"/intake/batches/{batch.id}/files/")

    def test_create_batch_can_save_only_the_registration(self):
        response = self.client.post(
            reverse("intake_module:batch_create"),
            {**self._batch_form_data("save_only"), "save_only": "1"},
        )
        batch = IntakeBatch.objects.get(code="save_only")
        self.assertRedirects(
            response,
            reverse("intake_module:batch_detail", args=[batch.id]),
            fetch_redirect_response=False,
        )

    def test_drive_listing_is_simulated_without_transfer_and_png_is_visible(self):
        client = FakeSelectionClient()
        with patch("web.intake_module.views.RcloneClient", return_value=client):
            with patch("gaiden.infrastructure.intake_drive.subprocess.run") as subprocess_run:
                response = self.client.post(
                    reverse("intake_module:batch_files", args=[self.batch.id]),
                    {"relative_folder": "Edgar_Rice_Burroughs", "drive_action": "list"},
                )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "edgar-rice-burroughs_tarzan-of-the-apes.epub")
        self.assertContains(response, "a-princess-of-mars.epub")
        self.assertContains(response, "cover.jpg")
        self.assertContains(response, "Ignorado nesta etapa")
        self.assertContains(response, "EPUB: 2")
        self.assertContains(response, "JPG: 1")
        self.assertEqual(self.batch.items.count(), 2)
        self.assertFalse(self.batch.items.exclude(status=IntakeState.DISCOVERED.value).exists())
        self.assertEqual(client.downloaded, [])
        subprocess_run.assert_not_called()

    def test_only_selected_drive_file_is_downloaded(self):
        client = FakeSelectionClient()
        report = discover_drive_folder(
            self.batch,
            self.batch.drive_relative_path,
            client=client,
        )
        tarzan_id = next(
            row["item_id"]
            for row in report["files"]
            if row["filename"] == "edgar-rice-burroughs_tarzan-of-the-apes.epub"
        )
        with patch("web.intake_module.views.RcloneClient", return_value=client):
            response = self.client.post(
                reverse("intake_module:batch_import_selected", args=[self.batch.id]),
                {"selected_items": [str(tarzan_id)]},
            )
        self.assertEqual(response.status_code, 302)
        tarzan = self.batch.items.get(pk=tarzan_id)
        other = self.batch.items.exclude(pk=tarzan_id).get()
        self.assertEqual(tarzan.status, IntakeState.DOWNLOADED.value)
        self.assertEqual(other.status, IntakeState.DISCOVERED.value)
        self.assertEqual(client.downloaded, ["edgar-rice-burroughs_tarzan-of-the-apes.epub"])

    def test_local_multiple_upload_uses_intake_storage(self):
        with patch("gaiden.infrastructure.intake_drive.subprocess.run") as subprocess_run:
            response = self.client.post(
                reverse("intake_module:batch_upload", args=[self.batch.id]),
                data={
                    "files": [
                        SimpleUploadedFile("one.txt", b"Book one", content_type="text/plain"),
                        SimpleUploadedFile("two.html", b"<p>Book two</p>", content_type="text/html"),
                    ]
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.batch.items.count(), 2)
        for item in self.batch.items.all():
            self.assertEqual(item.status, IntakeState.DOWNLOADED.value)
            self.assertTrue(intake_storage.resolve_stored_path(item.original_path).is_file())
        subprocess_run.assert_not_called()

    def test_drive_and_local_upload_share_download_storage_service(self):
        self.assertIs(drive_sync_service.store_downloaded_bytes, ingestion_service.store_downloaded_bytes)
        local = store_uploaded_files(self.batch, [FakeUpload("local.txt", b"same contract")])[0]["item"]
        client = FakeSelectionClient()
        report = discover_drive_folder(self.batch, self.batch.drive_relative_path, client=client)
        drive_item = self.batch.items.get(
            pk=next(row["item_id"] for row in report["files"] if row["compatible"])
        )
        download_drive_item(drive_item, client=client)
        for item in (local, drive_item):
            item.refresh_from_db()
            self.assertEqual(item.status, IntakeState.DOWNLOADED.value)
            self.assertEqual(
                item.source_sha256,
                hashlib.sha256(intake_storage.resolve_stored_path(item.original_path).read_bytes()).hexdigest(),
            )

    def test_one_failed_local_file_does_not_corrupt_the_next(self):
        results = store_uploaded_files(
            self.batch,
            [FakeUpload("empty.txt", b""), FakeUpload("valid.txt", b"Valid payload")],
        )
        self.assertIn("error", results[0])
        self.assertNotIn("error", results[1])
        failed = self.batch.items.get(source_filename="empty.txt")
        valid = self.batch.items.get(source_filename="valid.txt")
        self.assertEqual(failed.status, IntakeState.FAILED.value)
        self.assertEqual(valid.status, IntakeState.DOWNLOADED.value)
        self.assertTrue(intake_storage.resolve_stored_path(valid.original_path).is_file())

    def test_file_page_explains_when_rclone_is_unavailable(self):
        with patch("gaiden.infrastructure.intake_drive.shutil.which", return_value=None):
            with patch("gaiden.infrastructure.intake_drive.subprocess.run") as subprocess_run:
                response = self.client.get(reverse("intake_module:batch_files", args=[self.batch.id]))
        self.assertContains(response, "rclone")
        self.assertContains(response, "Não disponível")
        subprocess_run.assert_not_called()

    def test_batch_actions_are_shown_only_for_the_current_state(self):
        discovered = IntakeItem.objects.create(
            batch=self.batch,
            order_index=1,
            source_filename="pilot.epub",
            source_format="epub",
            status=IntakeState.DISCOVERED.value,
        )
        response = self.client.get(reverse("intake_module:item_detail", args=[discovered.id]))
        self.assertContains(response, "Baixar do Drive")
        self.assertNotContains(response, "Limpar e gerar clean.txt")
        response = self.client.get(reverse("intake_module:batch_detail", args=[self.batch.id]))
        self.assertContains(response, "Baixar próximo DISCOVERED")
        self.assertNotContains(response, "Limpar próximo DOWNLOADED")
        discovered.status = IntakeState.DOWNLOADED.value
        discovered.save(update_fields=["status", "updated_at"])
        response = self.client.get(reverse("intake_module:item_detail", args=[discovered.id]))
        self.assertNotContains(response, "Baixar do Drive")
        self.assertContains(response, "Limpar e gerar clean.txt")
        response = self.client.get(reverse("intake_module:batch_detail", args=[self.batch.id]))
        self.assertNotContains(response, "Baixar próximo DISCOVERED")
        self.assertContains(response, "Limpar próximo DOWNLOADED")

    def test_drive_pilot_advances_one_explicit_state_at_a_time(self):
        client = FakeDiscoveryClient()
        report = discover_drive_folder(self.batch, self.batch.drive_relative_path, client=client)
        self.assertEqual(len(report["discovered"]), 1)
        item = self.batch.items.get()
        self.assertEqual(item.source_filename, "edgar-rice-burroughs_tarzan-of-the-apes.epub")
        self.assertEqual(item.status, IntakeState.DISCOVERED.value)
        self.assertEqual(client.downloaded, 0)

        download_drive_item(item, client=client)
        item.refresh_from_db()
        self.assertEqual(item.status, IntakeState.DOWNLOADED.value)
        self.assertEqual(client.downloaded, 1)

        clean_downloaded_item(item, converter=FakeConverter())
        item.refresh_from_db()
        self.assertEqual(item.status, IntakeState.CLEAN_READY.value)
        prepare_for_codex(item, target_language="de")
        item.refresh_from_db()
        self.assertEqual(item.status, IntakeState.READY_FOR_CODEX.value)
        self.assertEqual(self.batch.items.count(), 1)

    def test_handoff_is_blocked_before_ready_for_editing(self):
        item = self._clean_item()
        with self.assertRaisesRegex(IntakeHandoffError, "READY_FOR_EDITING"):
            handoff_to_pipeline(item)
        self.assertFalse(Work.objects.filter(code=item.book_code).exists())

    def test_handoff_requires_all_individual_fields(self):
        item = self._ready_item()
        valid = {
            "confirmed_title": item.confirmed_title,
            "original_year": item.original_year,
            "book_code": item.book_code,
            "target_language": item.target_language,
        }
        empty = {"confirmed_title": "", "original_year": None, "book_code": "", "target_language": ""}
        for field, value in empty.items():
            with self.subTest(field=field):
                setattr(item, field, value)
                item.save(update_fields=[field, "updated_at"])
                with self.assertRaisesRegex(IntakeHandoffError, field):
                    handoff_to_pipeline(item)
                setattr(item, field, valid[field])
                item.save(update_fields=[field, "updated_at"])

    def test_handoff_creates_pipeline_records_and_canonical_paths(self):
        item = self._ready_item()
        result = handoff_to_pipeline(item)
        work = Work.objects.get(code="book_tarzan_apes")
        edition = Edition.objects.get(pk=result.edition.pk)
        template = BookEditionTemplate.objects.get(book_code="book_tarzan_apes", language="de")
        texts = EditionText.objects.get(edition=edition)
        pipeline = EditionPipeline.objects.get(edition=edition)

        expected_raw = storage.raw_source_path("book_tarzan_apes", "en", ".txt")
        expected_translated = storage.translated_dir("book_tarzan_apes", "de") / "clean_translate_de.txt"
        self.assertEqual(work.title, "Tarzan of the Apes")
        self.assertEqual(work.year, 1912)
        self.assertEqual(edition.language.code, "de")
        self.assertEqual(Path(edition.raw_source_path), expected_raw)
        self.assertEqual(Path(texts.raw_path), expected_raw)
        self.assertEqual(Path(template.source_saved_path), expected_raw)
        self.assertEqual(pipeline.current_stage, PipelineStage.TRANSLATED)
        self.assertEqual(Path(pipeline.core_last_txt_path), expected_translated)
        self.assertEqual(pipeline.translation_language, "de")
        self.assertEqual(expected_raw.read_text(encoding="utf-8"), "Chapter One\nTarzan begins.\n")
        self.assertEqual(
            expected_translated.read_text(encoding="utf-8"),
            "Kapitel Eins\nTarzan beginnt.\n",
        )
        item.refresh_from_db()
        self.assertEqual(item.handoff_raw_sha256, hashlib.sha256(expected_raw.read_bytes()).hexdigest())
        self.assertEqual(
            item.handoff_translated_sha256,
            hashlib.sha256(expected_translated.read_bytes()).hexdigest(),
        )

    def test_handoff_is_idempotent(self):
        item = self._ready_item()
        first = handoff_to_pipeline(item)
        first_counts = (
            Work.objects.count(),
            Edition.objects.count(),
            BookEditionTemplate.objects.count(),
            EditionText.objects.count(),
            EditionPipeline.objects.count(),
        )
        second = handoff_to_pipeline(item)
        self.assertEqual(first.edition.id, second.edition.id)
        self.assertEqual(second.created_files, ())
        self.assertEqual(
            first_counts,
            (
                Work.objects.count(),
                Edition.objects.count(),
                BookEditionTemplate.objects.count(),
                EditionText.objects.count(),
                EditionPipeline.objects.count(),
            ),
        )

    def test_book_code_conflict_blocks_without_overwriting(self):
        item = self._ready_item()
        language = Language.objects.create(code="en", name="English", native_name="English")
        author = Contributor.objects.create(name="Another Author")
        Work.objects.create(
            code=item.book_code,
            title="Another Work",
            original_language=language,
            author=author,
            year=1900,
        )
        with self.assertRaisesRegex(IntakeHandoffConflict, "different work"):
            handoff_to_pipeline(item)
        self.assertEqual(Work.objects.get(code=item.book_code).title, "Another Work")
        self.assertFalse(storage.raw_source_path(item.book_code, "en", ".txt").exists())

    def test_database_failure_rolls_back_records_and_partial_files(self):
        item = self._ready_item()
        raw_path = storage.raw_source_path(item.book_code, "en", ".txt")
        translated_path = storage.translated_dir(item.book_code, "de") / "clean_translate_de.txt"
        with patch(
            "gaiden.application.intake.pipeline_handoff._get_or_create_pipeline",
            side_effect=RuntimeError("database failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "database failure"):
                handoff_to_pipeline(item)
        self.assertFalse(Work.objects.filter(code=item.book_code).exists())
        self.assertFalse(Edition.objects.filter(work__code=item.book_code).exists())
        self.assertFalse(BookEditionTemplate.objects.filter(book_code=item.book_code).exists())
        self.assertFalse(raw_path.exists())
        self.assertFalse(translated_path.exists())

    def test_handoff_does_not_run_translate_frontmatter_covers_or_images(self):
        item = self._ready_item()
        with patch("pipeline.views.pipeline_translate_run") as translate:
            with patch("editorial.kdp_mode.build_frontmatter_files") as frontmatter:
                with patch("gaiden.infrastructure.storage.covers_dir") as covers:
                    with patch("gaiden.infrastructure.storage.images_dir") as images:
                        handoff_to_pipeline(item)
        translate.assert_not_called()
        frontmatter.assert_not_called()
        covers.assert_not_called()
        images.assert_not_called()

    def test_handoff_view_redirects_to_existing_pipeline_screen(self):
        item = self._ready_item()
        response = self.client.post(reverse("intake_module:item_handoff", args=[item.id]))
        edition_id = IntakeItem.objects.get(pk=item.id).handoff_edition_id
        self.assertRedirects(
            response,
            reverse("edition_steps", kwargs={"edition_id": edition_id}),
            fetch_redirect_response=False,
        )

    def test_metadata_can_be_saved_without_changing_item_state(self):
        item = self._clean_item()
        response = self.client.post(
            reverse("intake_module:item_update_metadata", args=[item.id]),
            {
                "confirmed_title": "Tarzan Updated",
                "original_year": 1914,
                "book_code": "book_tarzan_updated",
                "target_language": "fr",
            },
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.confirmed_title, "Tarzan Updated")
        self.assertEqual(item.status, IntakeState.CLEAN_READY.value)

    def test_bookmaker_button_replaces_codex_action_for_valid_clean_item(self):
        item = self._bookmaker_item()
        response = self.client.get(reverse("intake_module:item_detail", args=[item.id]))
        self.assertContains(response, "Abrir no Gaiden Bookmaker")

        item.status = IntakeState.CLEAN_READY.value
        item.save(update_fields=["status", "updated_at"])
        response = self.client.get(reverse("intake_module:item_detail", args=[item.id]))
        self.assertContains(response, "Abrir no Gaiden Bookmaker")
        self.assertNotContains(response, "Preparar para Codex")

        item.status = IntakeState.FAILED.value
        item.save(update_fields=["status", "updated_at"])
        response = self.client.get(reverse("intake_module:item_detail", args=[item.id]))
        self.assertNotContains(response, "Abrir no Gaiden Bookmaker")

    def test_duplicate_cannot_open_in_bookmaker(self):
        canonical = self._bookmaker_item()
        duplicate = IntakeItem.objects.create(
            batch=self.batch,
            duplicate_of=canonical,
            order_index=canonical.order_index + 1,
            source_filename="edgar-rice-burroughs_tarzan-of-the-apes (1).epub",
            source_format="epub",
            source_size=canonical.source_size,
            source_sha256=canonical.source_sha256,
            confirmed_title=canonical.confirmed_title,
            original_year=canonical.original_year,
            target_language=canonical.target_language,
            book_code="book_0032",
            original_path=canonical.original_path,
            status=IntakeState.DOWNLOADED.value,
        )
        response = self.client.get(reverse("intake_module:item_detail", args=[duplicate.id]))
        self.assertNotContains(response, "Abrir no Gaiden Bookmaker")
        with self.assertRaisesRegex(IntakeHandoffError, "canonical item"):
            open_in_bookmaker(duplicate)

    def test_open_in_bookmaker_creates_source_records_without_translation(self):
        item = self._bookmaker_item()
        with patch("pipeline.views.pipeline_translate_run") as translate:
            result = open_in_bookmaker(item)
        translate.assert_not_called()

        work = Work.objects.get(code="book_0031")
        edition = Edition.objects.get(pk=result.edition.pk)
        template = BookEditionTemplate.objects.get(book_code="book_0031", language="en")
        texts = EditionText.objects.get(edition=edition)
        pipeline = EditionPipeline.objects.get(edition=edition)
        item.refresh_from_db()

        self.assertEqual(work.title, "Tarzan of the Apes")
        self.assertEqual(work.author.name, "Edgar Rice Burroughs")
        self.assertEqual(work.year, 1912)
        self.assertEqual(work.original_language.code, "en")
        self.assertEqual(edition.title, "Tarzan of the Apes")
        self.assertEqual(edition.publisher, "RinoBooks")
        self.assertEqual(edition.editor, "Gaiden Editor")
        self.assertEqual(template.source_original_name, item.source_filename)
        self.assertEqual(template.source_file_sha256, item.source_sha256)
        self.assertEqual(template.collection_name, self.batch.collection_name)
        self.assertEqual(Path(template.source_saved_path), result.source_original_path)
        self.assertEqual(Path(edition.raw_source_path), result.canonical_text_path)
        self.assertEqual(Path(texts.raw_path), result.canonical_text_path)
        self.assertIn("Tarzan begins", texts.raw_text)
        self.assertEqual(pipeline.current_stage, "SOURCE_EXTRACTED")
        self.assertEqual(pipeline.translation_language, "en_us")
        self.assertIsNone(pipeline.translated_at)
        self.assertFalse(item.handoff_translated_path)
        self.assertEqual(item.handoff_edition_id, edition.id)

    def test_open_in_bookmaker_second_click_is_idempotent(self):
        item = self._bookmaker_item()
        first = open_in_bookmaker(item)
        counts = (
            Work.objects.count(),
            Edition.objects.count(),
            BookEditionTemplate.objects.count(),
            EditionText.objects.count(),
            EditionPipeline.objects.count(),
        )
        with patch(
            "gaiden.application.intake.pipeline_handoff.run_source_extract"
        ) as source_extract:
            second = open_in_bookmaker(item)
        source_extract.assert_not_called()
        self.assertEqual(first.edition.id, second.edition.id)
        self.assertFalse(second.created)
        self.assertEqual(
            counts,
            (
                Work.objects.count(),
                Edition.objects.count(),
                BookEditionTemplate.objects.count(),
                EditionText.objects.count(),
                EditionPipeline.objects.count(),
            ),
        )

    def test_open_in_bookmaker_redirects_to_steps_and_header_identifies_book(self):
        item = self._bookmaker_item(status=IntakeState.CLEAN_READY.value)
        response = self.client.post(
            reverse("intake_module:item_open_bookmaker", args=[item.id])
        )
        item.refresh_from_db()
        self.assertRedirects(
            response,
            reverse("edition_steps", kwargs={"edition_id": item.handoff_edition_id}),
            fetch_redirect_response=False,
        )

        response = self.client.get(
            reverse("edition_steps", kwargs={"edition_id": item.handoff_edition_id})
        )
        self.assertContains(response, "Livro 0031")
        self.assertContains(response, item.source_filename)
        self.assertContains(response, "Tarzan of the Apes")
        self.assertContains(response, "Edgar Rice Burroughs")
        self.assertContains(response, "Origem: EN")
        self.assertContains(response, "Destino: EN-US")
        self.assertContains(response, "Ano original: 1912")
        self.assertContains(response, "Coleção: Tarzan")
        self.assertNotContains(response, str(intake_storage.resolve_stored_path(item.original_path)))

    def test_existing_individual_upload_entrypoint_remains_accessible(self):
        response = self.client.get(reverse("book_edition_new"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cadastro do livro")

    def test_no_drive_subprocess_is_called_by_dashboard_or_upload(self):
        with patch("gaiden.infrastructure.intake_drive.subprocess.run") as run:
            self.client.get(reverse("root"))
            self.client.get(reverse("intake_module:batch_detail", args=[self.batch.id]))
        run.assert_not_called()
