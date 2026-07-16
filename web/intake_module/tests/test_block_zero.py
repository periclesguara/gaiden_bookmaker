import hashlib
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from editorial.models import Contributor, Edition, EditionPipeline, EditionText, Language, PipelineStage, Work
from gaiden.application.intake.drive_sync import discover_drive_folder, download_drive_item
from gaiden.application.intake.ingestion import clean_downloaded_item, ingest_bytes
from gaiden.application.intake.pipeline_handoff import (
    IntakeHandoffConflict,
    IntakeHandoffError,
    handoff_to_pipeline,
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

    def test_no_drive_subprocess_is_called_by_dashboard_or_upload(self):
        with patch("gaiden.infrastructure.intake_drive.subprocess.run") as run:
            self.client.get(reverse("root"))
            self.client.get(reverse("intake_module:batch_detail", args=[self.batch.id]))
        run.assert_not_called()
