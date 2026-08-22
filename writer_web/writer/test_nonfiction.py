from unittest.mock import patch

from django.test import TestCase

from writer_engine.engine import GenerationResult, NonfictionRequest
from writer.forms import ChapterForm, StoryProjectForm
from writer.models import Chapter, SourceDocument, StoryProject
from writer.services.generation import generate_chapter
from writer.services.projects import synchronize_chapters


class NonfictionWriterFlowTests(TestCase):
    def _chapter(self):
        project = StoryProject.objects.create(
            title="Capitalismo, socialismo e instituições",
            writing_mode=StoryProject.WritingMode.NONFICTION,
            chapter_count=1,
            vector_index_path="/runtime/nonfiction.jsonl",
        )
        synchronize_chapters(project)
        chapter = project.chapters.get()
        chapter.title = "Instituições e incentivos"
        chapter.direction = "Defender a tese institucional e delimitar o argumento."
        chapter.script = "Meu argumento inicial deve ser ampliado, sem ser substituído."
        chapter.source_guidance = "Consultar as fontes aprovadas sobre instituições."
        chapter.target_words = 400
        chapter.session_count = 1
        chapter.save()
        source = SourceDocument.objects.create(
            filename="institutions.txt",
            source_path="/sources/institutions.txt",
            status=SourceDocument.Status.NORMALIZED,
        )
        project.sources.add(source)
        chapter.reference_sources.add(source)
        return chapter

    @patch("writer.services.generation._engine")
    def test_nonfiction_needs_no_creative_bibles_and_audits_sources(self, engine_factory):
        chapter = self._chapter()
        engine_factory.return_value.create_nonfiction_chapter.return_value = GenerationResult(
            text="Texto desenvolvido.[^s1-1]\n\n## Fontes desta sessão\n\n"
            "[^s1-1]: source.txt — trecho chunk-1",
            model="test/qwen",
            source_chunk_ids=("chunk-1",),
            source_scores=(0.9,),
            attempts=1,
        )

        generate_chapter(chapter)

        chapter.refresh_from_db()
        self.assertEqual(chapter.status, Chapter.Status.GENERATION_COMPLETE)
        session = chapter.sessions.get()
        self.assertEqual(session.source_chunk_ids, ["chunk-1"])
        self.assertEqual(session.generation_parameters["writing_mode"], "NONFICTION")
        self.assertEqual(
            session.generation_parameters["citation_contract"],
            "rag-chunk-footnotes-v1",
        )
        self.assertEqual(
            session.generation_parameters["reference_source_ids"],
            list(chapter.reference_sources.values_list("id", flat=True)),
        )
        self.assertEqual(session.supporting_cast_snapshot, {})
        self.assertEqual(session.supporting_cast_sha256, "")
        engine_factory.return_value.create_chapter.assert_not_called()
        request = engine_factory.return_value.create_nonfiction_chapter.call_args.args[0]
        self.assertIsInstance(request, NonfictionRequest)
        self.assertEqual(request.operator_text, chapter.script)
        self.assertEqual(request.source_guidance, chapter.source_guidance)

    @patch("writer.services.generation._engine")
    def test_nonfiction_requires_an_exact_chapter_source(self, engine_factory):
        chapter = self._chapter()
        chapter.reference_sources.clear()

        with self.assertRaisesMessage(ValueError, "fontes deste capítulo"):
            generate_chapter(chapter)

        engine_factory.assert_not_called()

    def test_nonfiction_form_uses_operator_text_and_source_labels(self):
        form = ChapterForm(instance=self._chapter())

        self.assertEqual(
            form.fields["script"].label,
            "Texto-base, argumentos e notas a desenvolver",
        )
        self.assertEqual(form.fields["reference_sources"].label, "Fontes deste capítulo")
        self.assertIn("source_guidance", form.fields)

    def test_nonfiction_pt_br_contract_develops_instead_of_forcing_translation(self):
        form = StoryProjectForm(
            data={
                "title": "Ensaio",
                "writing_mode": StoryProject.WritingMode.NONFICTION,
                "language": StoryProject.Language.PT_BR,
                "chapter_count": 1,
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        project = form.save()
        self.assertEqual(project.language_contract["source_language"], "pt-BR")
        self.assertEqual(project.language_contract["target_language"], "pt-BR")
        self.assertEqual(project.language_contract["operation"], "original")
