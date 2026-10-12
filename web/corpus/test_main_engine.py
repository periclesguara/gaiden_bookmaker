from unittest import skipUnless
from unittest.mock import patch

from django.apps import apps
from django.test import TestCase

from .models import ProjectCorpus


@skipUnless(apps.get_app_config("writer").name == "writer", "Approved-main WriterEngine only")
class MainEngineIntegrationTests(TestCase):
    def setUp(self):
        self.project = apps.get_model("writer", "StoryProject").objects.create(title="Engine test", vector_index_path="legacy-index.jsonl")
        self.chapter = apps.get_model("writer", "Chapter")(project=self.project, title="Mystery")

    def test_canonical_engine_loads_selected_index_only(self):
        from writer.services.generation import _engine
        from .tests import FakeEmbedder
        embedder, index = FakeEmbedder(), object()
        with patch("corpus.embedding.configured_embedder", return_value=embedder), patch("corpus.services.selected_index", return_value=index) as selected, patch("writer.services.generation.VectorIndex.load") as legacy, patch("writer.services.generation.QwenGenerator"):
            engine = _engine(self.chapter)
        selected.assert_called_once_with(self.project, embedder)
        legacy.assert_not_called()
        self.assertIs(engine.index, index)
        with patch.object(embedder, "embed", return_value=[[1, 2, 3]]) as query:
            engine.embedder.embed(["clues"])
        query.assert_called_once_with(["search_query: clues"])

    def test_legacy_engine_keeps_recorded_index_and_existing_embedder(self):
        from writer.services.generation import _engine
        ProjectCorpus.objects.filter(project=self.project).update(backend="LEGACY_WRITER")
        self.project.refresh_from_db()
        with patch("corpus.embedding.configured_embedder") as configured, patch("corpus.services.selected_index") as selected, patch("writer.services.generation.VectorIndex.load") as legacy, patch("writer.services.generation.OpenAIEmbeddingClient") as embedding, patch("writer.services.generation.QwenGenerator"):
            engine = _engine(self.chapter)
        configured.assert_not_called()
        selected.assert_not_called()
        legacy.assert_called_once_with("legacy-index.jsonl")
        self.assertIs(engine.embedder, embedding.return_value)
