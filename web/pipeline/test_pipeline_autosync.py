from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from editorial.models import (
    Contributor,
    ContributorRole,
    Edition,
    EditionPipeline,
    Language,
    Seal,
    Work,
)


class EditionPipelineAutosyncTests(TestCase):
    def _create_edition(self, *, book_code: str = "book_0900", lang_code: str = "en", status: str = Edition.STATUS_REGISTERED) -> Edition:
        language, _ = Language.objects.get_or_create(
            code=lang_code,
            defaults={"name": "English", "native_name": "English", "is_active": True},
        )
        seal, _ = Seal.objects.get_or_create(
            slug="mantaquest",
            defaults={"name": "MantaQuest", "description": "", "is_active": True},
        )
        author, _ = Contributor.objects.get_or_create(
            name="Autosync Author",
            role=ContributorRole.AUTHOR,
        )
        work, _ = Work.objects.get_or_create(
            code=book_code,
            defaults={
                "title": book_code,
                "original_language": language,
                "author": author,
                "publisher": "RinoBooks",
                "source_format": "TXT",
            },
        )
        return Edition.objects.create(
            work=work,
            language=language,
            seal=seal,
            main_contributor=author,
            status=status,
            book_id=book_code,
            lang=lang_code,
        )

    def test_pipeline_row_created_on_edition_create(self):
        edition = self._create_edition()
        pipeline = EditionPipeline.objects.filter(edition=edition).first()
        self.assertIsNotNone(pipeline)
        self.assertEqual(pipeline.current_stage, edition.status)

    def test_pipeline_stage_updates_on_status_change(self):
        edition = self._create_edition(book_code="book_0901")
        edition.status = Edition.STATUS_PRETRUTH_READY
        edition.save(update_fields=["status", "updated_at"])
        pipeline = EditionPipeline.objects.get(edition=edition)
        self.assertEqual(pipeline.current_stage, Edition.STATUS_PRETRUTH_READY)

    def test_dashboard_includes_editions_without_pipeline(self):
        edition = self._create_edition(book_code="book_0902")
        EditionPipeline.objects.filter(edition=edition).delete()

        response = self.client.get(reverse("pipeline_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "book_0902")
        self.assertContains(response, "pipeline missing")

        rows = response.context["rows"]
        row = next(item for item in rows if item["edition"].id == edition.id)
        self.assertTrue(row["pipeline_missing"])
        self.assertEqual(row["current_stage"], edition.status)
