from unittest import skipUnless
from unittest.mock import patch

from django.apps import apps
from django.test import TestCase
from django.urls import reverse

from .models import ProjectCorpus


@skipUnless(apps.get_app_config("writer").name == "web.writer", "Operator contract workflow only")
class OperatorIntegrationTests(TestCase):
    def setUp(self):
        self.project = apps.get_model("writer", "StoryProject").objects.create(title="Operator test")
        self.chapter = apps.get_model("writer", "Chapter")(title="A detective mystery")

    def test_canonical_retrieval_does_not_use_legacy_chunks(self):
        from web.writer.services.source_retrieval import retrieve_source_rag
        with patch("corpus.writer_adapter.source_rag", return_value=[{"work_id": 7}]) as dense, patch("web.writer.services.source_retrieval._load_project_chunks") as legacy:
            self.assertEqual(retrieve_source_rag(self.project, self.chapter), [{"work_id": 7}])
        dense.assert_called_once_with(self.project, self.chapter, top_k=8, max_context_chars=24000)
        legacy.assert_not_called()

    def test_legacy_retrieval_does_not_call_dense_backend(self):
        from web.writer.services.source_retrieval import retrieve_source_rag
        ProjectCorpus.objects.filter(project=self.project).update(backend="LEGACY_WRITER")
        self.project.refresh_from_db()
        with patch("corpus.writer_adapter.source_rag") as dense, patch("web.writer.services.source_retrieval._load_project_chunks", return_value=[]) as legacy:
            self.assertEqual(retrieve_source_rag(self.project, self.chapter), [])
        legacy.assert_called_once_with(self.project, roles=None)
        dense.assert_not_called()

    def test_new_project_dashboard_has_zero_selected_works(self):
        from web.writer.services.dashboard import _reference_scope_status
        state = _reference_scope_status(self.project)
        self.assertEqual(state["selected"], 0)
        self.assertFalse(state["all_vectorized"])

    def test_legacy_selection_endpoint_cannot_mutate_canonical_project(self):
        response = self.client.post(reverse("writer:project_references", args=[self.project.pk]), {"corpus_selection": "1"})
        self.assertRedirects(response, reverse("corpus:project", args=[self.project.pk]), fetch_redirect_response=False)
        self.assertEqual(self.project.references.count(), 0)
