from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from editorial.models import EditionPipeline


class ModularNavigationTests(TestCase):
    def test_dashboard_exposes_independent_module_entrypoints(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        expected_links = (
            ("Writer", settings.WRITER_APP_URL),
            ("Intake", reverse("book_edition_list")),
            ("Bookmaker — Manual / AI", reverse("pipeline_jobs")),
            ("Projetos finalizados", "#projetos-finalizados"),
        )
        for label, url in expected_links:
            with self.subTest(label=label):
                self.assertContains(response, label)
                self.assertContains(response, f'href="{url}"')

    def test_dashboard_gets_have_no_side_effects(self):
        before = EditionPipeline.objects.count()
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get(reverse("pipeline_dashboard")).status_code, 200)
        self.assertEqual(EditionPipeline.objects.count(), before)

    def test_gaiden_has_no_writer_routes(self):
        response = self.client.get("/writer/")
        self.assertEqual(response.status_code, 404)
