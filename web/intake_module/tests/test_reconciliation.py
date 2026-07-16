import io
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from gaiden.application.intake.drive_sync import download_drive_item
from gaiden.application.intake.reconciliation import (
    ARTIFACT_CONFLICT,
    IntakeArtifactConflict,
    reconcile_batch_downloads,
)
from gaiden.domain.intake import IntakeState
from gaiden.infrastructure import intake_storage
from web.intake_module.models import IntakeBatch, IntakeItem


def epub_payload(text: str = "chapter") -> bytes:
    stream = io.BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("OEBPS/chapter.xhtml", f"<p>{text}</p>")
    return stream.getvalue()


class NeverDownloadClient:
    def __init__(self):
        self.calls = 0

    def check_available(self):
        self.calls += 1
        raise AssertionError("Drive must not be called when the final artifact is valid")


class IntakeReconciliationTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="gaiden-intake-reconcile-")
        self.addCleanup(temporary.cleanup)
        self.storage_root = Path(temporary.name) / "data"
        environment = patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.storage_root)}, clear=False)
        environment.start()
        self.addCleanup(environment.stop)
        self.batch = IntakeBatch.objects.create(
            code="recovery_batch",
            name="Recovery batch",
            source_language="en",
            drive_relative_path="01_INBOX_RAW/Recovery",
        )

    def create_item(
        self,
        order_index,
        filename,
        payload=None,
        *,
        status=IntakeState.FAILED.value,
        source_size=None,
    ):
        payload = payload if payload is not None else epub_payload(filename)
        item = IntakeItem.objects.create(
            batch=self.batch,
            order_index=order_index,
            source_filename=filename,
            source_format=Path(filename).suffix.lstrip("."),
            source_size=len(payload) if source_size is None else source_size,
            status=status,
            last_error="Refusing to overwrite intake artifact",
        )
        path = intake_storage.original_path(
            self.batch.code,
            self.batch.source_language,
            order_index,
            Path(filename).suffix,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return item, path, payload

    def test_valid_existing_file_is_adopted(self):
        item, path, payload = self.create_item(1, "book.epub")
        report = reconcile_batch_downloads(self.batch)
        item.refresh_from_db()
        self.assertEqual([row["item_id"] for row in report["adoptable"]], [item.id])
        self.assertEqual(item.status, IntakeState.DOWNLOADED.value)
        self.assertEqual(item.original_path, intake_storage.relative_storage_path(path))
        self.assertEqual(len(item.source_sha256), 64)
        self.assertEqual(item.last_error, "")
        self.assertEqual(path.read_bytes(), payload)

    def test_repeated_download_is_no_op_without_drive_copy(self):
        item, path, payload = self.create_item(
            1,
            "book.epub",
            status=IntakeState.DISCOVERED.value,
        )
        client = NeverDownloadClient()
        first = download_drive_item(item, client=client)
        second = download_drive_item(item, client=client)
        self.assertTrue(first["no_op"])
        self.assertTrue(second["no_op"])
        self.assertEqual(client.calls, 0)
        self.assertEqual(path.read_bytes(), payload)
        item.refresh_from_db()
        self.assertEqual(item.status, IntakeState.DOWNLOADED.value)

    def test_incompatible_file_is_never_overwritten(self):
        item, path, payload = self.create_item(
            1,
            "book.epub",
            status=IntakeState.DISCOVERED.value,
            source_size=999999,
        )
        client = NeverDownloadClient()
        with self.assertRaisesRegex(IntakeArtifactConflict, ARTIFACT_CONFLICT):
            download_drive_item(item, client=client)
        item.refresh_from_db()
        self.assertEqual(item.status, IntakeState.FAILED.value)
        self.assertIn(ARTIFACT_CONFLICT, item.last_error)
        self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(client.calls, 0)

    def test_corrupt_epub_is_not_adopted(self):
        item, path, payload = self.create_item(1, "broken.epub", payload=b"not-a-zip")
        report = reconcile_batch_downloads(self.batch)
        item.refresh_from_db()
        self.assertEqual(report["adoptable"], [])
        self.assertEqual(report["conflicts"][0]["item_id"], item.id)
        self.assertIn("invalid EPUB ZIP", report["conflicts"][0]["reason"])
        self.assertEqual(item.status, IntakeState.FAILED.value)
        self.assertEqual(path.read_bytes(), payload)

    def test_symlink_artifact_is_rejected_without_removal(self):
        payload = epub_payload("outside")
        item, path, _original = self.create_item(1, "linked.epub", payload=payload)
        outside = self.storage_root.parent / "outside.epub"
        outside.write_bytes(payload)
        path.unlink()
        path.symlink_to(outside)
        report = reconcile_batch_downloads(self.batch)
        item.refresh_from_db()
        self.assertIn("symlink", report["conflicts"][0]["reason"])
        self.assertEqual(item.status, IntakeState.FAILED.value)
        self.assertTrue(path.is_symlink())
        self.assertEqual(outside.read_bytes(), payload)

    def test_downloading_without_file_returns_to_discovered(self):
        item = IntakeItem.objects.create(
            batch=self.batch,
            order_index=1,
            source_filename="missing.epub",
            source_format="epub",
            source_size=100,
            status=IntakeState.DOWNLOADING.value,
        )
        report = reconcile_batch_downloads(self.batch)
        item.refresh_from_db()
        self.assertEqual(report["interrupted"][0]["item_id"], item.id)
        self.assertEqual(item.status, IntakeState.DISCOVERED.value)
        self.assertEqual(item.last_error, "download interrompido")

    def test_downloading_with_valid_file_becomes_downloaded(self):
        item, _path, _payload = self.create_item(
            1,
            "book.epub",
            status=IntakeState.DOWNLOADING.value,
        )
        reconcile_batch_downloads(self.batch)
        item.refresh_from_db()
        self.assertEqual(item.status, IntakeState.DOWNLOADED.value)

    def test_dry_run_does_not_change_database_or_files(self):
        item, path, payload = self.create_item(
            1,
            "book.epub",
            status=IntakeState.DOWNLOADING.value,
        )
        before = (
            item.status,
            item.original_path,
            item.source_sha256,
            item.last_error,
            item.updated_at,
        )
        report = reconcile_batch_downloads(self.batch, dry_run=True)
        item.refresh_from_db()
        after = (
            item.status,
            item.original_path,
            item.source_sha256,
            item.last_error,
            item.updated_at,
        )
        self.assertTrue(report["dry_run"])
        self.assertEqual(before, after)
        self.assertEqual(path.read_bytes(), payload)

    def test_download_uses_database_row_lock(self):
        item, _path, _payload = self.create_item(
            1,
            "book.epub",
            status=IntakeState.DISCOVERED.value,
        )
        with CaptureQueriesContext(connection) as queries:
            download_drive_item(item, client=NeverDownloadClient())
        self.assertTrue(any("FOR UPDATE" in query["sql"].upper() for query in queries.captured_queries))

    def test_copy_suffix_is_duplicate_of_unsuffixed_canonical_and_files_remain(self):
        payload = epub_payload("same work")
        duplicate, duplicate_path, duplicate_bytes = self.create_item(
            1,
            "jungle-tales-of-tarzan (1).epub",
            payload,
            status=IntakeState.DOWNLOADED.value,
        )
        canonical, canonical_path, canonical_bytes = self.create_item(
            2,
            "jungle-tales-of-tarzan.epub",
            payload,
            status=IntakeState.DOWNLOADED.value,
        )
        report = reconcile_batch_downloads(self.batch)
        duplicate.refresh_from_db()
        canonical.refresh_from_db()
        self.assertEqual(duplicate.duplicate_of_id, canonical.id)
        self.assertIsNone(canonical.duplicate_of_id)
        self.assertEqual(report["duplicates"][0]["duplicate_of_order_index"], 2)
        response = self.client.get(reverse("intake_module:batch_detail", args=[self.batch.id]))
        self.assertContains(response, "Duplicata do item 2")
        row = response.content.decode().split("jungle-tales-of-tarzan (1).epub", 1)[1].split("</tr>", 1)[0]
        self.assertNotIn(">Limpar<", row)
        self.assertEqual(duplicate_path.read_bytes(), duplicate_bytes)
        self.assertEqual(canonical_path.read_bytes(), canonical_bytes)

    def test_duplicate_item_never_offers_clean_or_prepare_for_codex(self):
        payload = epub_payload("same work")
        duplicate, _path, _bytes = self.create_item(
            1,
            "book (1).epub",
            payload,
            status=IntakeState.CLEAN_READY.value,
        )
        canonical, _canonical_path, _canonical_bytes = self.create_item(
            2,
            "book.epub",
            payload,
            status=IntakeState.CLEAN_READY.value,
        )
        reconcile_batch_downloads(self.batch)
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.duplicate_of_id, canonical.id)
        response = self.client.get(reverse("intake_module:item_detail", args=[duplicate.id]))
        self.assertContains(response, "Duplicata do item 2")
        self.assertNotContains(response, "Limpar e gerar clean.txt")
        self.assertNotContains(response, "Preparar para Codex")

    def test_reconciliation_preview_requires_confirmation_before_changes(self):
        item, _path, _payload = self.create_item(
            1,
            "book.epub",
            status=IntakeState.DOWNLOADING.value,
        )
        response = self.client.get(reverse("intake_module:batch_reconcile", args=[self.batch.id]))
        self.assertContains(response, "Prévia dry-run: nenhuma alteração foi realizada")
        self.assertContains(response, "Confirmar e executar reconciliação")
        item.refresh_from_db()
        self.assertEqual(item.status, IntakeState.DOWNLOADING.value)

    def test_batch_and_failed_item_show_reconciliation_actions(self):
        item, _path, _payload = self.create_item(
            7,
            "edgar-rice-burroughs_tarzan-of-the-apes.epub",
            status=IntakeState.FAILED.value,
        )
        batch_response = self.client.get(
            reverse("intake_module:batch_detail", args=[self.batch.id])
        )
        self.assertContains(batch_response, "Reconciliar arquivos importados")
        self.assertContains(batch_response, reverse("intake_module:item_reconcile", args=[item.id]))
        item_response = self.client.get(reverse("intake_module:item_detail", args=[item.id]))
        self.assertContains(item_response, "Reconciliar este item")

    def test_conflicting_item_does_not_show_reconciliation_action(self):
        item, _path, _payload = self.create_item(
            1,
            "conflict.epub",
            status=IntakeState.FAILED.value,
            source_size=999999,
        )
        batch_response = self.client.get(
            reverse("intake_module:batch_detail", args=[self.batch.id])
        )
        self.assertNotContains(batch_response, "Reconciliar arquivos importados")
        self.assertNotContains(
            batch_response,
            reverse("intake_module:item_reconcile", args=[item.id]),
        )
        item_response = self.client.get(reverse("intake_module:item_detail", args=[item.id]))
        self.assertNotContains(item_response, "Reconciliar este item")

    def test_item_get_is_preview_only_and_post_adopts_without_copy_or_download(self):
        self.create_item(
            1,
            "another-valid-book.epub",
            status=IntakeState.DOWNLOADED.value,
        )
        item, path, payload = self.create_item(
            7,
            "edgar-rice-burroughs_tarzan-of-the-apes.epub",
            status=IntakeState.FAILED.value,
        )
        url = reverse("intake_module:item_reconcile", args=[item.id])
        before_updated_at = item.updated_at
        get_response = self.client.get(url)
        self.assertContains(get_response, "FAILED → DOWNLOADED")
        self.assertContains(get_response, "Confirmar e executar reconciliação")
        item.refresh_from_db()
        self.assertEqual(item.status, IntakeState.FAILED.value)
        self.assertEqual(item.updated_at, before_updated_at)

        with patch("gaiden.infrastructure.intake_drive.subprocess.run") as subprocess_run:
            with patch("gaiden.infrastructure.intake_storage.atomic_write_bytes") as write_bytes:
                post_response = self.client.post(url, {"confirm": "1"}, follow=True)
        item.refresh_from_db()
        self.assertRedirects(
            post_response,
            reverse("intake_module:batch_detail", args=[self.batch.id]),
        )
        self.assertContains(post_response, "reconciliado com sucesso")
        self.assertEqual(item.status, IntakeState.DOWNLOADED.value)
        self.assertEqual(item.original_path, intake_storage.relative_storage_path(path))
        self.assertEqual(len(item.source_sha256), 64)
        self.assertEqual(item.last_error, "")
        self.assertEqual(path.read_bytes(), payload)
        subprocess_run.assert_not_called()
        write_bytes.assert_not_called()

    def test_second_item_reconciliation_post_is_a_true_no_op(self):
        item, path, payload = self.create_item(
            7,
            "edgar-rice-burroughs_tarzan-of-the-apes.epub",
            status=IntakeState.FAILED.value,
        )
        url = reverse("intake_module:item_reconcile", args=[item.id])
        self.client.post(url, {"confirm": "1"})
        item.refresh_from_db()
        first_state = (
            item.status,
            item.original_path,
            item.source_sha256,
            item.last_error,
            item.updated_at,
        )
        with patch("gaiden.infrastructure.intake_drive.subprocess.run") as subprocess_run:
            with patch("gaiden.infrastructure.intake_storage.atomic_write_bytes") as write_bytes:
                response = self.client.post(url, {"confirm": "1"}, follow=True)
        item.refresh_from_db()
        second_state = (
            item.status,
            item.original_path,
            item.source_sha256,
            item.last_error,
            item.updated_at,
        )
        self.assertEqual(first_state, second_state)
        self.assertContains(response, "nenhuma alteração necessária")
        self.assertEqual(path.read_bytes(), payload)
        subprocess_run.assert_not_called()
        write_bytes.assert_not_called()
