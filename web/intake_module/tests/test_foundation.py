import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from gaiden.domain.intake import IntakeState, InvalidIntakeTransition, transition_state
from gaiden.infrastructure import intake_storage
from web.intake_module.models import IntakeBatch, IntakeItem


class IntakeFoundationTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="gaiden-intake-foundation-")
        self.addCleanup(temporary.cleanup)
        self.storage_root = Path(temporary.name) / "data"
        environment = patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.storage_root)}, clear=False)
        environment.start()
        self.addCleanup(environment.stop)

    def create_batch(self, code="batch_0001"):
        return IntakeBatch.objects.create(code=code, name="Independent Books", source_language="en")

    def test_batch_can_be_created_without_individual_titles(self):
        batch = self.create_batch()
        item = IntakeItem.objects.create(
            batch=batch, order_index=1, source_filename="one.epub", source_format="epub"
        )
        self.assertEqual(item.suggested_title, "")
        self.assertEqual(item.confirmed_title, "")
        self.assertIsNone(item.original_year)
        self.assertEqual(item.book_code, "")

    def test_batch_accepts_multiple_independent_items(self):
        batch = self.create_batch()
        IntakeItem.objects.create(batch=batch, order_index=1, source_filename="one.txt", source_format="txt")
        IntakeItem.objects.create(batch=batch, order_index=2, source_filename="two.html", source_format="html")
        self.assertEqual(list(batch.items.values_list("order_index", flat=True)), [1, 2])

    def test_item_order_is_unique_inside_batch(self):
        batch = self.create_batch()
        IntakeItem.objects.create(batch=batch, order_index=1, source_filename="one.txt", source_format="txt")
        with self.assertRaises(IntegrityError), transaction.atomic():
            IntakeItem.objects.create(batch=batch, order_index=1, source_filename="two.txt", source_format="txt")

    def test_storage_is_isolated_under_intake_namespace(self):
        path = intake_storage.batch_root("batch_0001", "en")
        self.assertEqual(path, self.storage_root / "intake" / "batch_0001" / "en")
        self.assertNotIn("/raw/", str(path))
        self.assertNotIn("/collections/", str(path))

    def test_storage_rejects_path_traversal(self):
        with self.assertRaises(intake_storage.IntakeStorageError):
            intake_storage.batch_root("../escape", "en")

    def test_atomic_write_refuses_silent_overwrite(self):
        path = intake_storage.clean_path("batch_0001", "en", 1)
        intake_storage.atomic_write_text(path, "first")
        with self.assertRaises(FileExistsError):
            intake_storage.atomic_write_text(path, "second")
        self.assertEqual(path.read_text(encoding="utf-8"), "first")

    def test_invalid_state_transition_is_rejected(self):
        with self.assertRaises(InvalidIntakeTransition):
            transition_state(IntakeState.DISCOVERED, IntakeState.READY_FOR_EDITING)

    def test_basic_pages(self):
        batch = self.create_batch()
        item = IntakeItem.objects.create(
            batch=batch, order_index=1, source_filename="one.txt", source_format="txt"
        )
        for url in (
            reverse("intake_module:batch_list"),
            reverse("intake_module:batch_create"),
            reverse("intake_module:batch_detail", args=[batch.id]),
            reverse("intake_module:item_detail", args=[item.id]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
