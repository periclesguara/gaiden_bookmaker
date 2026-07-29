from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from gaiden.application.intake.automated import build_automated_editorial_plan
from gaiden.domain.intake import IntakeState
from web.intake_module.models import IntakeBatch, IntakeItem


def candidate(**overrides):
    batch = SimpleNamespace(
        code="batch_1984",
        author_default="George Orwell",
        source_language="en",
        public_domain=True,
    )
    values = {
        "id": 41,
        "batch": batch,
        "confirmed_title": "1984",
        "suggested_title": "",
        "book_code": "book_0041",
        "original_year": 1949,
        "duplicate_of_id": None,
        "status": IntakeState.DOWNLOADED.value,
        "original_path": "intake/batch_1984/en/originals/0001.epub",
        "source_sha256": "a" * 64,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class AutomatedEditorialPlanTests(SimpleTestCase):
    def test_builds_six_language_editions_for_orwell_candidate(self):
        plan = build_automated_editorial_plan(candidate())

        self.assertEqual(plan["status"], "ready")
        self.assertTrue(plan["read_only"])
        self.assertEqual(
            [edition["language"] for edition in plan["editions"]],
            ["en-gb", "fr-fr", "pt-br", "it-it", "de-de", "es-es"],
        )
        self.assertEqual(plan["editions"][0]["source_action"], "localize")
        self.assertEqual(plan["editions"][0]["end_marker"], "THE END")
        self.assertEqual(plan["editions"][2]["end_marker"], "FIM")
        self.assertEqual(len(plan["plan_sha256"]), 64)

    def test_reserved_tarzan_range_is_blocked(self):
        plan = build_automated_editorial_plan(candidate(book_code="book_0037"))

        self.assertEqual(plan["status"], "blocked")
        self.assertIn("reservado para Tarzan", plan["errors"][0])

    def test_missing_provenance_blocks_plan(self):
        plan = build_automated_editorial_plan(
            candidate(original_path="", source_sha256="", status=IntakeState.DISCOVERED.value)
        )

        self.assertEqual(plan["status"], "blocked")
        self.assertGreaterEqual(len(plan["errors"]), 3)

    def test_plan_is_deterministic(self):
        self.assertEqual(
            build_automated_editorial_plan(candidate()),
            build_automated_editorial_plan(candidate()),
        )


class AutomatedEditorialPlanViewTests(TestCase):
    def setUp(self):
        batch = IntakeBatch.objects.create(
            code="batch_1984",
            name="George Orwell",
            author_default="George Orwell",
            source_language="en",
            public_domain=True,
        )
        self.item = IntakeItem.objects.create(
            batch=batch,
            order_index=1,
            source_filename="1984.epub",
            source_format="epub",
            source_size=100,
            source_sha256="a" * 64,
            suggested_title="1984",
            confirmed_title="1984",
            original_year=1949,
            book_code="book_0041",
            original_path="intake/batch_1984/en/originals/0001.epub",
            status=IntakeState.DOWNLOADED.value,
        )

    def test_preview_is_read_only_and_lists_six_editions(self):
        url = reverse("intake_module:item_automated_preview", args=[self.item.id])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["plan"]["status"], "ready")
        self.assertContains(response, "en-gb")
        self.assertContains(response, "fr-fr")
        self.assertContains(response, "pt-br")
        self.assertContains(response, "it-it")
        self.assertContains(response, "de-de")
        self.assertContains(response, "es-es")
        self.assertEqual(IntakeItem.objects.get(pk=self.item.pk).status, IntakeState.DOWNLOADED.value)
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_plan_can_be_downloaded_as_json(self):
        response = self.client.get(
            reverse("intake_module:item_automated_plan", args=[self.item.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "automated")
        self.assertTrue(response.json()["read_only"])
        self.assertIn("attachment;", response["Content-Disposition"])
