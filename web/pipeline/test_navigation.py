from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from editorial.models import EditionPipeline
from writer.models import ChapterSession, SourceDocument, StoryProject


class ModularNavigationTests(TestCase):
    def setUp(self):
        self.staff_user = get_user_model().objects.create_user(
            username="navigation-editor",
            password="test-password",
            is_staff=True,
        )

    def test_dashboard_exposes_current_module_entrypoints(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        expected_links = (
            ("Writer", reverse("writer:home")),
            ("Intake", reverse("book_edition_list")),
            ("Bookmaker — Manual / AI", reverse("pipeline_jobs")),
            ("Projetos finalizados", "#projetos-finalizados"),
        )
        for label, url in expected_links:
            with self.subTest(label=label):
                self.assertContains(response, label)
                self.assertContains(response, f'href="{url}"')

    def test_writer_entrypoint_opens_the_current_staff_writer(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("writer:home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "writer/home.html")

    def test_fiction_and_nonfiction_remain_available(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse("writer:project_new"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="FICTION"')
        self.assertContains(response, "Fiction — Ficção")
        self.assertContains(response, 'value="NONFICTION"')
        self.assertContains(response, "Nonfiction — Não ficção")

    def test_dashboard_gets_have_no_side_effects(self):
        before = (
            EditionPipeline.objects.count(),
            StoryProject.objects.count(),
            SourceDocument.objects.count(),
            ChapterSession.objects.count(),
        )

        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get(reverse("pipeline_dashboard")).status_code, 200)

        self.assertEqual(
            (
                EditionPipeline.objects.count(),
                StoryProject.objects.count(),
                SourceDocument.objects.count(),
                ChapterSession.objects.count(),
            ),
            before,
        )

    @patch("writer.views.vectorize_project")
    def test_non_staff_user_cannot_run_protected_writer_operation(self, vectorize_project):
        response = self.client.post(reverse("writer:vectorize", args=[999]))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.url)
        vectorize_project.assert_not_called()

    @patch("writer.views.generate_supporting_characters_bible")
    @patch("writer.views.vectorize_project")
    @patch("writer.views.generate_chapter")
    def test_navigation_gets_do_not_start_model_or_rag_work(
        self,
        generate_chapter,
        vectorize_project,
        generate_supporting_characters_bible,
    ):
        self.client.force_login(self.staff_user)

        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get(reverse("writer:home")).status_code, 200)

        generate_chapter.assert_not_called()
        vectorize_project.assert_not_called()
        generate_supporting_characters_bible.assert_not_called()
