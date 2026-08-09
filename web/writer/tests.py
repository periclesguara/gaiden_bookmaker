from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from gaiden.writer_engine.engine import GenerationResult
from writer.forms import StoryProjectForm
from writer.language_contract import (
    apply_deterministic_rules,
    contract_sha256,
    default_language_contract,
    generated_text_violations,
    language_contract_for,
    validate_language_contract,
)
from writer.models import Chapter, ChapterSession, SourceDocument, StoryProject
from writer.services.generation import generate_chapter
from writer.services.normalization import normalize_document, normalize_text
from writer.services.projects import synchronize_chapters
from writer.services.sources import discover_source_documents


class NormalizationTests(TestCase):
    def test_gutenberg_contract_is_removed_and_narrative_epilogue_is_preserved(self):
        raw = (
            "Project Gutenberg metadata and legal terms\n"
            "*** START OF THE PROJECT GUTENBERG EBOOK A TEST ***\n\n"
            "TITLE PAGE\n\nCHAPTER I\n\n" + ("The narrative begins here. " * 30)
            + "\n\nEPILOGUE\n\n" + ("The narrative closes here. " * 25)
            + "\n*** END OF THE PROJECT GUTENBERG EBOOK A TEST ***\n"
            "Project Gutenberg license"
        )
        result = normalize_text(raw)
        self.assertNotIn("legal terms", result.text)
        self.assertNotIn("Project Gutenberg license", result.text)
        self.assertIn("CHAPTER I", result.text)
        self.assertIn("EPILOGUE", result.text)
        self.assertEqual(result.provider, "PROJECT_GUTENBERG")

    def test_unknown_source_is_not_cut_at_arbitrary_heading(self):
        raw = ("Authorial opening material. " * 30) + "\n\nCHAPTER I\n\n" + ("Body. " * 100)
        result = normalize_text(raw)
        self.assertTrue(result.text.startswith("Authorial opening material"))

    def test_normalized_file_is_external_and_audited(self):
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "input.txt"
            source.write_text("CHAPTER I\n\n" + ("Body text. " * 100), encoding="utf-8")
            document = SourceDocument.objects.create(filename=source.name, source_path=str(source))
            storage = Path(temporary) / "writer-storage"
            with patch.dict(os.environ, {"GAIDEN_WRITER_STORAGE_ROOT": str(storage)}):
                normalize_document(document)
            document.refresh_from_db()
            self.assertEqual(document.status, SourceDocument.Status.NORMALIZED)
            self.assertTrue(Path(document.normalized_path).is_file())
            self.assertEqual(len(document.normalized_sha256), 64)
            self.assertIn("normalized_characters", document.normalization_report)


class LanguageContractTests(TestCase):
    def test_default_contract_is_first_en_us_semantic_creation_profile(self):
        contract = default_language_contract()
        self.assertEqual(contract["source_language"], "en-GB")
        self.assertEqual(contract["target_language"], "en-US")
        self.assertEqual(contract["operation"], "original")
        self.assertTrue(contract["reference_policy"]["semantic_content_only"])
        self.assertFalse(contract["reference_policy"]["imitate_source_style"])
        self.assertFalse(contract["reference_policy"]["preserve_victorianism"])
        self.assertTrue(contract["style"]["american_english_only"])
        self.assertEqual(contract["style"]["reduce_archaisms"], "strong")

    def test_selector_contracts_cover_all_three_languages(self):
        en_us = language_contract_for("en-US")
        en_gb = language_contract_for("en-GB")
        pt_br = language_contract_for("pt-BR")
        self.assertTrue(en_us["style"]["american_english_only"])
        self.assertEqual(en_gb["target_variant"], "Contemporary British English")
        self.assertEqual(pt_br["operation"], "translate_and_modernize")
        self.assertEqual(pt_br["target_language"], "pt-BR")

    def test_project_form_loads_contract_from_language_selector(self):
        form = StoryProjectForm(data={
            "title": "Portuguese edition",
            "language": "pt-BR",
            "premise": "",
            "character_bible": "",
            "antagonist_bible": "",
            "scenario_bible": "",
            "world_bible": "",
            "story_direction": "",
            "story_outline": "",
            "chapter_count": 1,
        })
        self.assertTrue(form.is_valid(), form.errors)
        project = form.save()
        self.assertEqual(project.language, "pt-BR")
        self.assertEqual(project.language_contract["target_language"], "pt-BR")
        self.assertEqual(project.language_contract["operation"], "translate_and_modernize")

    def test_language_selector_is_immutable_after_first_session(self):
        project = StoryProject.objects.create(title="Started book", chapter_count=1)
        chapter = Chapter.objects.create(project=project, number=1)
        ChapterSession.objects.create(
            chapter=chapter,
            number=1,
            status=ChapterSession.Status.COMPLETE,
            content="Draft",
        )
        form = StoryProjectForm(
            instance=project,
            data={
                "title": project.title,
                "language": "en-GB",
                "premise": "",
                "character_bible": "",
                "antagonist_bible": "",
                "scenario_bible": "",
                "world_bible": "",
                "story_direction": "",
                "story_outline": "",
                "chapter_count": 1,
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("language", form.errors)

    def test_contract_applies_exact_rules_and_rejects_forbidden_terms(self):
        contract = default_language_contract()
        contract["deleted_terms"] = ["decerto"]
        contract["forbidden_terms"] = ["amiúde"]
        contract["replacements"] = {"deveras": "realmente"}
        validate_language_contract(contract)

        result = apply_deterministic_rules(
            "Deveras, isto decerto flui. Amiúde retorna.", contract
        )
        self.assertIn("Realmente, isto flui.", result)
        violations = generated_text_violations(result, contract, target_words=5)
        self.assertTrue(any("amiúde" in violation.casefold() for violation in violations))
        self.assertEqual(len(contract_sha256(contract)), 64)

    def test_contract_rejects_unknown_fields(self):
        contract = default_language_contract()
        contract["regra_digitada_errada"] = True
        with self.assertRaisesMessage(Exception, "campos desconhecidos"):
            validate_language_contract(contract)

    def test_contract_rejects_wrong_enum_types(self):
        contract = default_language_contract()
        contract["operation"] = []
        with self.assertRaisesMessage(Exception, "operation deve ser um texto"):
            validate_language_contract(contract)

        contract = default_language_contract()
        contract["style"]["fluency"] = []
        with self.assertRaisesMessage(Exception, "style.fluency deve ser um texto"):
            validate_language_contract(contract)

    def test_contract_rejects_replacement_cascades(self):
        contract = default_language_contract()
        contract["replacements"] = {"archaic": "modern"}
        contract["deleted_terms"] = ["modern"]
        with self.assertRaisesMessage(Exception, "deleted_terms"):
            validate_language_contract(contract)

        contract = default_language_contract()
        contract["replacements"] = {"archaic": "modern", "modern": "plain"}
        with self.assertRaisesMessage(Exception, "encadeadas"):
            validate_language_contract(contract)

    def test_contract_rejects_unsupported_output_language(self):
        contract = default_language_contract()
        contract["target_language"] = "ja-JP"
        with self.assertRaisesMessage(Exception, "en-US, en-GB ou pt-BR"):
            validate_language_contract(contract)


class SourceDiscoveryTests(TestCase):
    def test_discovery_registers_supported_files_only(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sherlock.md").write_text("text", encoding="utf-8")
            (root / "cover.jpg").write_bytes(b"not indexed")
            with patch.dict(os.environ, {"GAIDEN_WRITER_SOURCE_ROOT": str(root)}):
                self.assertEqual(discover_source_documents(), 1)
                self.assertEqual(discover_source_documents(), 0)
            self.assertEqual(SourceDocument.objects.get().filename, "sherlock.md")


class ProjectAndChapterTests(TestCase):
    def _project(self, **overrides):
        values = {
            "title": "New Mystery",
            "character_bible": "Detective character facts",
            "antagonist_bible": "Antagonist character facts",
            "scenario_bible": "London locations",
            "world_bible": "Victorian period, cold climate",
            "story_direction": "A fair-play investigation",
            "story_outline": "Ten chapter causal outline",
            "chapter_count": 10,
        }
        values.update(overrides)
        return StoryProject.objects.create(**values)

    def test_project_creates_ten_parameter_rows_without_deleting_existing_chapters(self):
        project = self._project()
        synchronize_chapters(project)
        self.assertEqual(project.chapters.count(), 10)
        self.assertEqual(project.chapters.get(number=1).target_words, 2500)
        self.assertEqual(project.chapters.get(number=1).session_count, 4)
        project.chapter_count = 12
        project.save()
        synchronize_chapters(project)
        self.assertEqual(project.chapters.count(), 12)

    @patch("writer.services.generation._engine")
    def test_generation_runs_four_sessions_then_requires_explicit_finalization(self, engine_factory):
        contract = default_language_contract()
        contract["replacements"] = {"Original": "Modernized"}
        project = self._project(
            chapter_count=1,
            vector_index_path="/runtime/index.jsonl",
            language_contract=contract,
        )
        synchronize_chapters(project)
        chapter = project.chapters.get()
        chapter.direction = "Investigate the locked room"
        chapter.script = "Opening, clue, confrontation, resolution"
        chapter.save()

        class FakeEngine:
            def create_chapter(self, request, *, top_k):
                number = len(engine_factory.return_value.calls) + 1
                engine_factory.return_value.calls.append(request)
                return GenerationResult(
                    text=(f"Modernized session {number}. " * 208),
                    model="test/qwen",
                    source_chunk_ids=(f"source-{number}",),
                    source_scores=(0.9,),
                )

        engine_factory.return_value = FakeEngine()
        engine_factory.return_value.calls = []
        generate_chapter(chapter)
        chapter.refresh_from_db()
        self.assertEqual(chapter.status, Chapter.Status.GENERATION_COMPLETE)
        self.assertEqual(chapter.sessions.count(), 4)
        self.assertFalse(chapter.final_text)
        chapter.finalize()
        chapter.refresh_from_db()
        self.assertEqual(chapter.status, Chapter.Status.FINAL)
        self.assertIn("Modernized session 1", chapter.final_text)
        self.assertNotIn("Original session", chapter.final_text)
        first_session = chapter.sessions.get(number=1)
        self.assertEqual(first_session.language_contract, contract)
        self.assertEqual(first_session.language_contract_sha256, contract_sha256(contract))
        self.assertEqual(
            engine_factory.return_value.calls[0].language_contract["target_language"], "en-US"
        )

    @patch("writer.services.generation._engine")
    def test_incompatible_session_does_not_leave_chapter_generating(self, engine_factory):
        project = self._project(
            chapter_count=1,
            vector_index_path="/runtime/index.jsonl",
        )
        synchronize_chapters(project)
        chapter = project.chapters.get()
        chapter.direction = "Investigate"
        chapter.script = "Opening and resolution"
        chapter.save()
        ChapterSession.objects.create(
            chapter=chapter,
            number=1,
            status=ChapterSession.Status.COMPLETE,
            content="Legacy draft",
            language_contract={},
            language_contract_sha256="",
        )

        with self.assertRaisesMessage(ValueError, "different or legacy"):
            generate_chapter(chapter)

        chapter.refresh_from_db()
        self.assertEqual(chapter.status, Chapter.Status.PLANNED)
        engine_factory.return_value.create_chapter.assert_not_called()

    def test_finalize_rejects_incomplete_sessions(self):
        project = self._project(chapter_count=1)
        synchronize_chapters(project)
        chapter = project.chapters.get()
        ChapterSession.objects.create(
            chapter=chapter, number=1, status=ChapterSession.Status.COMPLETE, content="draft"
        )
        with self.assertRaisesMessage(ValueError, "all configured sessions"):
            chapter.finalize()


class WriterViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="writer-editor", password="test-password", is_staff=True
        )
        self.client.force_login(self.user)
        self.project = StoryProject.objects.create(title="Book", chapter_count=2)
        synchronize_chapters(self.project)

    def test_writer_pages_are_reachable_without_side_effects(self):
        before = (StoryProject.objects.count(), Chapter.objects.count())
        for name, args in (
            ("writer:home", ()),
            ("writer:sources", ()),
            ("writer:project_detail", (self.project.id,)),
        ):
            response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, 200)
        self.assertEqual((StoryProject.objects.count(), Chapter.objects.count()), before)

    def test_normalize_and_generate_actions_require_post(self):
        document = SourceDocument.objects.create(filename="x.txt", source_path="/missing/x.txt")
        chapter = self.project.chapters.first()
        self.assertEqual(
            self.client.get(reverse("writer:normalize_sources")).status_code, 405
        )
        self.assertEqual(
            self.client.get(reverse("writer:generate", args=[chapter.id])).status_code, 405
        )
        self.assertEqual(
            self.client.get(reverse("writer:finalize", args=[chapter.id])).status_code, 405
        )

    def test_critical_writer_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        chapter = self.project.chapters.first()
        self.assertEqual(
            client.post(reverse("writer:generate", args=[chapter.id])).status_code, 403
        )


    def test_project_form_renders_each_editorial_field_in_its_own_box(self):
        response = self.client.get(reverse("writer:project_new"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bíblias e direção")
        self.assertContains(response, "Título do projeto")
        self.assertContains(response, "Idioma de escrita")
        self.assertContains(response, "Premissa")
        self.assertContains(response, "Bíblia do personagem")
        self.assertContains(response, "Bíblia do antagonista")
        self.assertContains(response, "Cenários e locais")
        self.assertContains(response, "Mundo, época, clima e referências")
        self.assertContains(response, "Direção da história")
        self.assertContains(response, "Roteiro geral")
        self.assertContains(response, "Quantidade de capítulos")
        self.assertContains(response, "Salvar e criar tabela de capítulos")
        self.assertEqual(response.content.count(b'class="field-box'), 10)

    def test_project_form_saves_bibles_and_creates_chapter_table(self):
        response = self.client.post(
            reverse("writer:project_new"),
            {
                "title": "Sherlock Holmes — The Devil in Paris",
                "language": "en-US",
                "premise": "A Paris mystery.",
                "character_bible": "Holmes and Watson continuity.",
                "antagonist_bible": "The Devil's motive and method.",
                "scenario_bible": "Paris locations.",
                "world_bible": "Contemporary historical frame.",
                "story_direction": "A concise fair-play investigation.",
                "story_outline": "Clues, reversal, confrontation, resolution.",
                "chapter_count": 12,
            },
        )

        project = StoryProject.objects.get(title="Sherlock Holmes — The Devil in Paris")
        self.assertRedirects(
            response, reverse("writer:project_detail", args=[project.id])
        )
        self.assertEqual(project.language, "en-US")
        self.assertEqual(project.chapters.count(), 12)
        self.assertEqual(
            list(project.chapters.values_list("number", flat=True)),
            list(range(1, 13)),
        )
