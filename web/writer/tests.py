from __future__ import annotations

import hashlib
import os
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from gaiden.writer_engine.engine import GenerationResult
from author_studio.models import Author, CanonicalText, Work, WorkSource
from web.writer.models import Chapter, ChapterSession, SourceDocument, StoryProject
from web.writer.services.dashboard import _model_status, build_project_dashboard
from web.writer.services.generation import generate_chapter
from web.writer.services.normalization import normalize_document, normalize_text
from web.writer.services.projects import synchronize_chapters
from web.writer.services.sources import discover_source_documents


class WriterAnonymousAccessTests(SimpleTestCase):
    @patch("web.writer.views.SourceDocument.objects.count", return_value=0)
    @patch("web.writer.views.StoryProject.objects.prefetch_related", return_value=())
    def test_home_opens_directly_without_authentication(self, projects, source_count):
        response = self.client.get(reverse("writer:home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "writer/home.html")


class DashboardTests(SimpleTestCase):
    class Related:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    def test_completed_rag_moves_dashboard_to_bibles_and_explains_generation_block(self):
        with TemporaryDirectory() as temporary:
            index_path = Path(temporary) / "project.jsonl"
            index_path.write_text(json.dumps({
                "schema_version": 1,
                "embedding_model": "qwen3-embedding:0.6b",
                "dimension": 1024,
                "source_count": 2,
                "chunk_count": 18,
                "created_at": "2026-08-09T00:00:00+00:00",
            }) + "\n", encoding="utf-8")
            sources = [
                SimpleNamespace(
                    status=SourceDocument.Status.VECTORIZED,
                    normalized_path=f"/canonical/{number}.txt",
                    normalized_sha256=str(number) * 64,
                    vectorized_at=object(),
                )
                for number in (1, 2)
            ]
            chapter = SimpleNamespace(
                direction="",
                script="",
                status=Chapter.Status.PLANNED,
                sessions=self.Related([]),
            )
            project = SimpleNamespace(
                sources=self.Related(sources),
                chapters=self.Related([chapter]),
                vector_index_path=str(index_path),
                character_bible="",
                antagonist_bible="",
                scenario_bible="",
                world_bible="",
                story_direction="",
                story_outline="",
            )
            with patch.dict(os.environ, {
                "GAIDEN_EMBEDDING_MODEL": "qwen3-embedding:0.6b",
                "GAIDEN_QWEN_MODEL": "qwen3.5:9b-q4_K_M",
            }):
                dashboard = build_project_dashboard(project, probe_models=False)

        self.assertEqual(dashboard["completed_stages"], 2)
        self.assertEqual(dashboard["current_stage"]["number"], 3)
        self.assertTrue(dashboard["sources"]["all_normalized"])
        self.assertTrue(dashboard["sources"]["all_vectorized"])
        self.assertEqual(dashboard["index"]["chunk_count"], 18)
        self.assertFalse(dashboard["chapter_rows"][0]["generation_ready"])
        self.assertIn(
            "Preencher bíblia do personagem",
            dashboard["chapter_rows"][0]["generation_blockers"],
        )

    @patch("web.writer.services.dashboard.urlopen")
    def test_separate_local_endpoints_are_verified_independently(self, urlopen_mock):
        embedding_response = urlopen_mock.return_value.__enter__.return_value
        embedding_response.read.side_effect = [
            json.dumps({"data": [{"id": "Qwen/Qwen3-Embedding-0.6B"}]}).encode(),
            json.dumps({"data": [{"id": "Qwen/Qwen3.5-9B"}]}).encode(),
        ]
        with patch.dict(os.environ, {
            "GAIDEN_EMBEDDING_BASE_URL": "http://127.0.0.1:8001/v1",
            "GAIDEN_EMBEDDING_MODEL": "Qwen/Qwen3-Embedding-0.6B",
            "GAIDEN_QWEN_BASE_URL": "http://127.0.0.1:8000/v1",
            "GAIDEN_QWEN_MODEL": "Qwen/Qwen3.5-9B",
        }, clear=False):
            status = _model_status(probe=True)
        self.assertTrue(status["embedding_online"])
        self.assertTrue(status["writing_online"])
        self.assertTrue(status["embedding_available"])
        self.assertTrue(status["writing_available"])
        self.assertTrue(status["online"])
        self.assertEqual(urlopen_mock.call_count, 2)


class NormalizationServiceTests(SimpleTestCase):
    def test_author_studio_provenance_survives_explicit_renormalization(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "canonical.txt"
            source.write_text("CHAPTER I\n\n" + ("Canonical narrative. " * 80), encoding="utf-8")
            document = SimpleNamespace(
                source_path=str(source),
                provider="AUTHOR_STUDIO",
                save=lambda: None,
            )
            with patch(
                "web.writer.services.normalization.writer_storage_root",
                return_value=root / "writer",
            ):
                normalize_document(document)

        self.assertEqual(document.provider, "AUTHOR_STUDIO")
        self.assertEqual(document.status, SourceDocument.Status.NORMALIZED)


class IdempotentActionTests(SimpleTestCase):
    def test_completed_stage_button_reports_success(self):
        project = SimpleNamespace(id=2)
        dashboard = {
            "stages": [{
                "number": 1,
                "label": "Normalizar",
                "complete": True,
                "detail": "9 de 9 fontes normalizadas",
            }]
        }
        with (
            patch("web.writer.views.get_object_or_404", return_value=project),
            patch("web.writer.views.build_project_dashboard", return_value=dashboard),
            patch("web.writer.views.messages.success") as success,
        ):
            response = self.client.post(
                reverse("writer:stage_status", args=[project.id, 1])
            )

        self.assertEqual(response.status_code, 302)
        success.assert_called_once()
        self.assertIn("Normalização concluída", success.call_args.args[1])

    def test_completed_vectorization_confirmation_does_not_rebuild_index(self):
        project = SimpleNamespace(id=2)
        dashboard = {
            "sources": {"all_vectorized": True, "vectorized": 9},
            "index": {"chunk_count": 803},
            "vectorize_blockers": [],
        }
        with (
            patch("web.writer.views.get_object_or_404", return_value=project),
            patch("web.writer.views.build_project_dashboard", return_value=dashboard),
            patch("web.writer.views.vectorize_project") as vectorize_project_mock,
            patch("web.writer.views.messages.success") as success,
        ):
            response = self.client.post(
                reverse("writer:vectorize", args=[project.id]), {"confirm": "yes"}
            )

        self.assertEqual(response.status_code, 302)
        vectorize_project_mock.assert_not_called()
        self.assertIn("Vetorização concluída", success.call_args.args[1])

    def test_completed_vectorization_remake_rebuilds_only_after_explicit_choice(self):
        project = SimpleNamespace(id=2)
        dashboard = {
            "sources": {"all_vectorized": True, "vectorized": 9},
            "index": {"chunk_count": 803},
            "vectorize_blockers": [],
        }
        with (
            patch("web.writer.views.get_object_or_404", return_value=project),
            patch("web.writer.views.build_project_dashboard", return_value=dashboard),
            patch("web.writer.views.vectorize_project") as vectorize_project_mock,
            patch("web.writer.views.messages.success") as success,
        ):
            response = self.client.post(
                reverse("writer:vectorize", args=[project.id]), {"remake": "yes"}
            )

        self.assertEqual(response.status_code, 302)
        vectorize_project_mock.assert_called_once_with(project)
        self.assertIn("Revetorização concluída", success.call_args.args[1])


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

    def test_ready_author_studio_canonical_is_immediately_selectable(self):
        with TemporaryDirectory() as temporary:
            media_root = Path(temporary)
            relative_path = Path("author_studio/ACD-SHER3/canonical.txt")
            canonical_path = media_root / relative_path
            canonical_path.parent.mkdir(parents=True)
            content = "CHAPTER I\n\n" + ("Sherlock Holmes investigated. " * 40)
            canonical_path.write_text(content, encoding="utf-8")
            checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
            author = Author.objects.create(
                name="Arthur Conan Doyle",
                canonical_name="arthur conan doyle",
                slug="arthur-conan-doyle",
                code="ACD",
            )
            work = Work.objects.create(
                author=author,
                title="The Adventures of Sherlock Holmes",
                canonical_title="the adventures of sherlock holmes",
                slug="the-adventures-of-sherlock-holmes",
                code="ACD-SHER3",
                status="CANONICAL_READY",
            )
            source = WorkSource.objects.create(
                work=work,
                code="ACD-SHER3-SRC001",
                original_filename="sherlock.epub",
                stored_file="author_studio/ACD-SHER3/sherlock.epub",
                extension=".epub",
                size_bytes=1,
                sha256="0" * 64,
                extraction_status="EXTRACTED",
            )
            CanonicalText.objects.create(
                work=work,
                source=source,
                code="ACD-SHER3-CAN001",
                text_file=str(relative_path),
                sha256=checksum,
                character_count=len(content),
                word_count=len(content.split()),
                status="READY",
            )

            with override_settings(MEDIA_ROOT=media_root), patch.dict(
                os.environ, {"GAIDEN_WRITER_SOURCE_ROOT": ""}
            ):
                self.assertEqual(discover_source_documents(), 1)

            document = SourceDocument.objects.get()
            self.assertEqual(document.provider, "AUTHOR_STUDIO")
            self.assertEqual(document.status, SourceDocument.Status.NORMALIZED)
            self.assertEqual(document.normalized_path, str(canonical_path))
            self.assertIn("ACD-SHER3", document.filename)


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

    @patch("web.writer.services.generation._engine")
    def test_generation_runs_four_sessions_then_requires_explicit_finalization(self, engine_factory):
        project = self._project(chapter_count=1, vector_index_path="/runtime/index.jsonl")
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
                    text=(f"Original session {number}. " * 220),
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
        self.assertIn("Original session 1", chapter.final_text)

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
        self.project = StoryProject.objects.create(title="Book", chapter_count=2)
        synchronize_chapters(self.project)

    def test_anonymous_writer_pages_open_directly_without_side_effects(self):
        before = (StoryProject.objects.count(), Chapter.objects.count())
        for name, args in (
            ("writer:home", ()),
            ("writer:sources", ()),
            ("writer:project_detail", (self.project.id,)),
            ("writer:works", ()),
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
        chapter = self.project.chapters.first()
        self.assertEqual(
            client.post(reverse("writer:generate", args=[chapter.id])).status_code, 403
        )
