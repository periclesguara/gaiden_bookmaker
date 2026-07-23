import json
import os
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from editorial.models import Contributor, Language, Work
from gaiden.application.intake.book_code_allocation import (
    BookCodeManifestConflict,
    StaleBookCodePlan,
    allocation_manifest_path,
    preview_book_code_allocation,
    reserve_book_codes,
)
from gaiden.domain.intake import IntakeState
from web.intake_module.forms import IntakeItemMetadataForm
from web.intake_module.models import BookCodeSequence, IntakeBatch, IntakeItem


class BookCodeAllocationTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="gaiden-book-code-allocation-")
        self.addCleanup(temporary.cleanup)
        self.storage_root = Path(temporary.name) / "data"
        environment = patch.dict(
            os.environ,
            {"GAIDEN_STORAGE_ROOT": str(self.storage_root)},
            clear=False,
        )
        environment.start()
        self.addCleanup(environment.stop)
        BookCodeSequence.objects.update_or_create(name="book", defaults={"next_number": 33})

    def create_batch(self, code="batch_0004"):
        return IntakeBatch.objects.create(
            code=code,
            name="Edgar Rice Burroughs",
            author_default="Edgar Rice Burroughs",
            source_language="en",
        )

    def create_item(self, batch, order_index, **overrides):
        defaults = {
            "source_filename": f"title-{order_index}.epub",
            "source_format": "epub",
            "source_size": 1024,
            "source_sha256": f"{order_index:064x}",
            "suggested_title": f"Title {order_index}",
            "status": IntakeState.DISCOVERED.value,
        }
        defaults.update(overrides)
        return IntakeItem.objects.create(
            batch=batch,
            order_index=order_index,
            **defaults,
        )

    def create_work(self, code, title):
        language, _ = Language.objects.get_or_create(
            code="en",
            defaults={"name": "English", "native_name": "English"},
        )
        author = Contributor.objects.create(name="Edgar Rice Burroughs")
        return Work.objects.create(
            code=code,
            title=title,
            original_language=language,
            author=author,
        )

    def test_reserves_book_0033_through_book_0041_in_order_index_order(self):
        batch = self.create_batch()
        for order_index in (7, 2, 9, 1, 5, 3, 8, 4, 6):
            self.create_item(batch, order_index)

        plan = preview_book_code_allocation(batch)
        self.assertEqual(plan["start_code"], "book_0033")
        self.assertEqual(plan["end_code"], "book_0041")
        result = reserve_book_codes(batch, plan_sha256=plan["plan_sha256"], actor="operator")

        self.assertEqual(result["allocated"], [f"book_{number:04d}" for number in range(33, 42)])
        self.assertEqual(
            list(batch.items.order_by("order_index").values_list("book_code", flat=True)),
            [f"book_{number:04d}" for number in range(33, 42)],
        )
        batch.refresh_from_db()
        self.assertEqual(batch.book_codes_start, "book_0033")
        self.assertEqual(batch.book_codes_end, "book_0041")
        self.assertEqual(batch.book_codes_allocated_count, 9)

    def test_preserves_existing_codes_and_excludes_known_duplicate(self):
        batch = self.create_batch()
        first = self.create_item(batch, 1, book_code="book_0031")
        second = self.create_item(batch, 2, book_code="book_0032")
        canonical = self.create_item(batch, 3)
        duplicate = self.create_item(
            batch,
            4,
            source_sha256=canonical.source_sha256,
            duplicate_of=canonical,
        )

        plan = preview_book_code_allocation(batch)
        reserve_book_codes(batch, plan_sha256=plan["plan_sha256"])

        first.refresh_from_db()
        second.refresh_from_db()
        canonical.refresh_from_db()
        duplicate.refresh_from_db()
        self.assertEqual(first.book_code, "book_0031")
        self.assertEqual(second.book_code, "book_0032")
        self.assertEqual(canonical.book_code, "book_0033")
        self.assertEqual(duplicate.book_code, "")

    def test_is_idempotent_after_all_eligible_items_are_numbered(self):
        batch = self.create_batch()
        self.create_item(batch, 1)
        first_plan = preview_book_code_allocation(batch)
        reserve_book_codes(batch, plan_sha256=first_plan["plan_sha256"])
        sequence_before = BookCodeSequence.objects.get(name="book").next_number

        second_plan = preview_book_code_allocation(batch)
        result = reserve_book_codes(batch, plan_sha256=second_plan["plan_sha256"])

        self.assertTrue(result["no_op"])
        self.assertEqual(BookCodeSequence.objects.get(name="book").next_number, sequence_before)
        self.assertEqual(batch.items.get().book_code, "book_0033")

    def test_existing_canonical_code_is_never_reused(self):
        self.create_work("book_0033", "Already Registered")
        batch = self.create_batch()
        self.create_item(batch, 1)
        plan = preview_book_code_allocation(batch)
        self.assertEqual(plan["start_code"], "book_0034")

    def test_two_overlapping_previews_cannot_both_reserve_same_range(self):
        first_batch = self.create_batch("batch_0004")
        second_batch = self.create_batch("batch_0005")
        self.create_item(first_batch, 1)
        self.create_item(second_batch, 1)
        first_plan = preview_book_code_allocation(first_batch)
        second_plan = preview_book_code_allocation(second_batch)
        self.assertEqual(first_plan["start_code"], second_plan["start_code"])

        reserve_book_codes(first_batch, plan_sha256=first_plan["plan_sha256"])
        with self.assertRaises(StaleBookCodePlan):
            reserve_book_codes(second_batch, plan_sha256=second_plan["plan_sha256"])
        self.assertEqual(second_batch.items.get().book_code, "")

    def test_preview_is_read_only_and_stale_batch_mutation_is_rejected(self):
        batch = self.create_batch()
        item = self.create_item(batch, 1)
        plan = preview_book_code_allocation(batch)
        self.assertEqual(item.book_code, "")
        self.assertFalse(allocation_manifest_path(batch).exists())

        item.confirmed_title = "Changed after preview"
        item.save(update_fields=["confirmed_title", "updated_at"])
        with self.assertRaises(StaleBookCodePlan):
            reserve_book_codes(batch, plan_sha256=plan["plan_sha256"])
        self.assertEqual(IntakeItem.objects.get(pk=item.pk).book_code, "")

    def test_manifest_is_projected_only_after_database_commit(self):
        batch = self.create_batch()
        self.create_item(batch, 1)
        plan = preview_book_code_allocation(batch)
        path = allocation_manifest_path(batch)
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            result = reserve_book_codes(
                batch,
                plan_sha256=plan["plan_sha256"],
                actor="operator",
            )
            self.assertFalse(path.exists())
        batch.refresh_from_db()
        self.assertEqual(
            batch.book_code_manifest["items"][0]["book_code"],
            "book_0033",
        )
        self.assertEqual(result["manifest"], batch.book_code_manifest)
        for callback in callbacks:
            callback()
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["start_code"], "book_0033")
        self.assertEqual(payload["items"][0]["book_code"], "book_0033")
        self.assertEqual(payload["updated_by"], "operator")
        self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_reservation_does_not_call_drive_download_or_cleaning(self):
        batch = self.create_batch()
        self.create_item(batch, 1)
        plan = preview_book_code_allocation(batch)
        with (
            patch("gaiden.infrastructure.intake_drive.RcloneClient") as rclone,
            patch("gaiden.application.intake.drive_sync.download_drive_item") as download,
            patch("gaiden.application.intake.ingestion.clean_downloaded_item") as clean,
        ):
            reserve_book_codes(batch, plan_sha256=plan["plan_sha256"])
        rclone.assert_not_called()
        download.assert_not_called()
        clean.assert_not_called()

    def test_stale_filesystem_manifest_is_replaced_after_commit(self):
        batch = self.create_batch()
        first = self.create_item(batch, 1)
        second = self.create_item(batch, 2)
        plan = preview_book_code_allocation(batch)
        path = allocation_manifest_path(batch)
        path.parent.mkdir(parents=True)
        path.write_text('{"batch_code":"stale"}', encoding="utf-8")

        with self.captureOnCommitCallbacks(execute=True):
            reserve_book_codes(batch, plan_sha256=plan["plan_sha256"])

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.book_code, "book_0033")
        self.assertEqual(second.book_code, "book_0034")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["batch_code"], batch.code)
        self.assertEqual(payload["allocated_count"], 2)

    def test_incremental_allocation_rebuilds_consolidated_manifest(self):
        batch = self.create_batch()
        self.create_item(batch, 1)
        first_plan = preview_book_code_allocation(batch)
        reserve_book_codes(batch, plan_sha256=first_plan["plan_sha256"])

        self.create_item(batch, 2)
        second_plan = preview_book_code_allocation(batch)
        result = reserve_book_codes(batch, plan_sha256=second_plan["plan_sha256"])

        batch.refresh_from_db()
        self.assertEqual(result["allocated"], ["book_0034"])
        self.assertEqual(batch.book_codes_start, "book_0033")
        self.assertEqual(batch.book_codes_end, "book_0034")
        self.assertEqual(batch.book_codes_allocated_count, 2)
        self.assertEqual(
            [row["book_code"] for row in batch.book_code_manifest["items"]],
            ["book_0033", "book_0034"],
        )

    def test_registered_work_is_linked_without_advancing_sequence(self):
        work = self.create_work("book_0040", "Existing Work")
        batch = self.create_batch()
        item = self.create_item(
            batch,
            1,
            suggested_title=work.title,
        )
        plan = preview_book_code_allocation(batch)
        self.assertEqual(plan["rows"][0]["action"], "link")
        before = BookCodeSequence.objects.get(name="book").next_number

        result = reserve_book_codes(batch, plan_sha256=plan["plan_sha256"])

        item.refresh_from_db()
        self.assertEqual(result["allocated"], ["book_0040"])
        self.assertEqual(item.book_code, "book_0040")
        self.assertEqual(BookCodeSequence.objects.get(name="book").next_number, before)

    def test_nonempty_book_code_is_unique(self):
        first_batch = self.create_batch("batch_0004")
        second_batch = self.create_batch("batch_0005")
        self.create_item(first_batch, 1, book_code="book_0031")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_item(second_batch, 1, book_code="book_0031")

    def test_preview_confirm_and_manifest_views(self):
        batch = self.create_batch()
        self.create_item(batch, 1)
        preview_url = reverse("intake_module:batch_book_codes_preview", args=[batch.id])
        response = self.client.get(preview_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirmar reserva de book_0033 a book_0033")
        plan = response.context["plan"]
        response = self.client.post(
            reverse("intake_module:batch_book_codes_confirm", args=[batch.id]),
            {"plan_sha256": plan["plan_sha256"]},
            follow=True,
        )
        self.assertContains(response, "Reserva concluída")
        export = self.client.get(
            reverse("intake_module:batch_book_codes_manifest", args=[batch.id])
        )
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export["Content-Type"], "application/json")

    def test_batch_detail_filters_numbered_duplicate_and_handed_off_items(self):
        batch = self.create_batch()
        numbered = self.create_item(batch, 1, book_code="book_0031")
        canonical = self.create_item(batch, 2)
        duplicate = self.create_item(batch, 3, duplicate_of=canonical)
        handed_off = self.create_item(batch, 4, handoff_edition_id=123)
        url = reverse("intake_module:batch_detail", args=[batch.id])

        expectations = {
            "without": {canonical.id, duplicate.id, handed_off.id},
            "numbered": {numbered.id},
            "duplicates": {duplicate.id},
            "handed_off": {handed_off.id},
        }
        for selected_filter, expected_ids in expectations.items():
            with self.subTest(selected_filter=selected_filter):
                response = self.client.get(url, {"book_code_filter": selected_filter})
                self.assertEqual(
                    {item.id for item in response.context["items"]},
                    expected_ids,
                )

    def test_manual_code_validation_and_existing_code_immutability(self):
        batch = self.create_batch()
        unnumbered = self.create_item(batch, 1)
        invalid = IntakeItemMetadataForm(
            {
                "confirmed_title": "Title",
                "original_year": 1912,
                "book_code": "BOOK-33",
                "target_language": "en",
            },
            instance=unnumbered,
        )
        self.assertFalse(invalid.is_valid())
        self.assertIn("book_code", invalid.errors)

        numbered = self.create_item(batch, 2, book_code="book_0031")
        immutable = IntakeItemMetadataForm(
            {
                "confirmed_title": "Updated title",
                "original_year": 1912,
                "book_code": "book_9999",
                "target_language": "en",
            },
            instance=numbered,
        )
        self.assertTrue(immutable.is_valid())
        saved = immutable.save()
        self.assertEqual(saved.book_code, "book_0031")


class BookCodeAllocationConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_confirmations_allocate_only_one_overlapping_preview(self):
        temporary = tempfile.TemporaryDirectory(prefix="gaiden-book-code-concurrency-")
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(
            os.environ,
            {"GAIDEN_STORAGE_ROOT": str(Path(temporary.name) / "data")},
            clear=False,
        )
        environment.start()
        self.addCleanup(environment.stop)
        BookCodeSequence.objects.update_or_create(
            name="book",
            defaults={"next_number": 33},
        )
        batches = []
        plans = []
        for suffix in (4, 5):
            batch = IntakeBatch.objects.create(
                code=f"batch_{suffix:04d}",
                name=f"Batch {suffix}",
                source_language="en",
            )
            IntakeItem.objects.create(
                batch=batch,
                order_index=1,
                source_filename=f"title-{suffix}.epub",
                source_format="epub",
                source_size=100,
                source_sha256=f"{suffix:064x}",
                suggested_title=f"Title {suffix}",
                status=IntakeState.DISCOVERED.value,
            )
            batches.append(batch)
            plans.append(preview_book_code_allocation(batch))

        barrier = threading.Barrier(2)
        outcomes = []

        def confirm(batch_id, token):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                result = reserve_book_codes(
                    IntakeBatch.objects.get(pk=batch_id),
                    plan_sha256=token,
                )
                outcomes.append(("allocated", result["allocated"]))
            except StaleBookCodePlan:
                outcomes.append(("stale", []))
            finally:
                close_old_connections()

        threads = [
            threading.Thread(
                target=confirm,
                args=(batch.id, plan["plan_sha256"]),
            )
            for batch, plan in zip(batches, plans)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sorted(kind for kind, _codes in outcomes), ["allocated", "stale"])
        self.assertEqual(
            list(
                IntakeItem.objects.exclude(book_code="").values_list(
                    "book_code", flat=True
                )
            ),
            ["book_0033"],
        )
