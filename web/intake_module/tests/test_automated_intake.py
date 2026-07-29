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
        "clean_path": "",
        "source_sha256": "a" * 64,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class AutomatedEditorialPlanTests(SimpleTestCase):
    def test_builds_english_uk_and_portuguese_brazil_pilot(self):
        plan = build_automated_editorial_plan(candidate())

        self.assertEqual(plan["status"], "ready")
        self.assertTrue(plan["read_only"])
        self.assertEqual(
            [edition["locale"] for edition in plan["editions"]],
            ["en-gb", "pt-br"],
        )
        self.assertEqual(
            [edition["language"] for edition in plan["editions"]],
            ["en", "pt-br"],
        )
        self.assertEqual(
            [edition["pipeline_language"] for edition in plan["editions"]],
            ["en", "ptbr"],
        )
        self.assertEqual(plan["editions"][0]["source_action"], "localize_en_gb")
        self.assertEqual(plan["editions"][0]["end_marker"], "THE END")
        self.assertEqual(plan["editions"][1]["end_marker"], "FIM")
        self.assertEqual(len(plan["plan_sha256"]), 64)

    def test_tarzan_reserved_codes_are_valid_when_already_assigned(self):
        for book_code in ("book_0034", "book_0037", "book_0040"):
            with self.subTest(book_code=book_code):
                plan = build_automated_editorial_plan(candidate(book_code=book_code))
                self.assertEqual(plan["status"], "ready")
                self.assertEqual(plan["source"]["book_code"], book_code)

    def test_downloaded_source_plans_cleaning_before_editions(self):
        plan = build_automated_editorial_plan(candidate())

        self.assertEqual(
            [stage["name"] for stage in plan["preparation_stages"]],
            ["clean_source"],
        )

    def test_clean_ready_requires_clean_path(self):
        plan = build_automated_editorial_plan(
            candidate(status=IntakeState.CLEAN_READY.value, clean_path="")
        )

        self.assertEqual(plan["status"], "blocked")
        self.assertIn("clean.txt", " ".join(plan["errors"]))

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

    def test_preview_is_read_only_and_lists_two_editions(self):
        url = reverse("intake_module:item_automated_preview", args=[self.item.id])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["plan"]["status"], "ready")
        self.assertContains(response, "en-gb")
        self.assertContains(response, "pt-br")
        self.assertNotContains(response, "fr-fr")
        self.assertEqual(IntakeItem.objects.get(pk=self.item.pk).status, IntakeState.DOWNLOADED.value)
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_plan_can_be_downloaded_as_json(self):
        response = self.client.get(
            reverse("intake_module:item_automated_plan", args=[self.item.id])
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["mode"], "automated")
        self.assertEqual(payload["pilot"], "en-gb_pt-br")
        self.assertEqual(
            [edition["locale"] for edition in payload["editions"]],
            ["en-gb", "pt-br"],
        )
        self.assertTrue(payload["read_only"])
        self.assertIn("attachment;", response["Content-Disposition"])
