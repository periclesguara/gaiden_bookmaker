from __future__ import annotations

import hashlib
import os
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from gaiden.writer_engine.engine import GenerationResult
from author_studio.models import Author, CanonicalText, Work, WorkSource
from editorial.models import Contributor, Edition, EditionPipeline, Language, Seal, Work as EditorialWork
from pipeline.services.writer_intake_handoff import register_closed_writer_in_intake
from web.writer.models import Chapter, ChapterSession, ManuscriptRevision, ProjectReference, SourceDocument, StoryProject
from web.writer.services.dashboard import build_project_dashboard
from web.writer.services.generation import generate_chapter
from web.writer.services.normalization import normalize_document, normalize_text
from web.writer.services.projects import synchronize_chapters
from web.writer.services.sources import discover_source_documents
from web.writer.services.finalization import (edition_source, export_merge, prepare_edition_source,
                                             save_final)
from web.writer.services.manuscript_drive import _BoundedBuffer, import_drive_final, list_final_files
from web.writer.services.manuscript_pipeline import save_merged_manuscript
from web.writer.services.manuscripts import ProjectClosedError, close_writer_project, import_manuscript_revision, promote_canonical_manuscript


class WriterAnonymousAccessTests(SimpleTestCase):
    @patch("web.writer.views.SourceDocument.objects.count", return_value=0)
    @patch("web.writer.views.StoryProject.objects.prefetch_related", return_value=())
    def test_home_opens_directly_without_authentication(self, projects, source_count):
        response = self.client.get(reverse("writer:home"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "writer/home.html")


class FinalManuscriptTests(TestCase):
    def setUp(self):
        self.project = StoryProject.objects.create(title="Final book", chapter_count=1)

    def upload(self, name="approved.md", content=b"# Chapter 1\n\nApproved text."):
        return SimpleUploadedFile(name, content, content_type="text/markdown")

    def test_existing_project_defaults_to_writing_and_upload_is_versioned(self):
        self.assertEqual(self.project.status, StoryProject.Status.WRITING)
        with TemporaryDirectory() as root, patch.dict(os.environ, {"GAIDEN_WRITER_STORAGE_ROOT": root}):
            first = import_manuscript_revision(self.project, self.upload())
            second = import_manuscript_revision(self.project, self.upload("revised.txt", b"CHAPTER ONE\n\nRevised."))
            self.assertEqual((first.version, second.version), (1, 2))
            self.assertNotEqual(Path(first.storage_path).read_bytes(), Path(second.storage_path).read_bytes())
            self.assertEqual(first.sha256, hashlib.sha256(Path(first.storage_path).read_bytes()).hexdigest())
            self.assertEqual(first.word_count, 5)

    def test_canonical_and_close_create_manifest_without_touching_chapter(self):
        chapter = Chapter.objects.create(project=self.project, number=1, final_text="kept byte-for-byte")
        session = ChapterSession.objects.create(chapter=chapter, number=1, status=ChapterSession.Status.COMPLETE, content="session")
        with TemporaryDirectory() as root, patch.dict(os.environ, {"GAIDEN_WRITER_STORAGE_ROOT": root}):
            revision = import_manuscript_revision(self.project, self.upload())
            promote_canonical_manuscript(self.project, revision)
            close_writer_project(self.project)
            chapter.refresh_from_db()
            session.refresh_from_db()
            self.project.refresh_from_db()
            self.assertEqual(chapter.final_text, "kept byte-for-byte")
            self.assertEqual(session.content, "session")
            self.assertEqual(self.project.status, StoryProject.Status.CLOSED)
            manifest = Path(revision.storage_path).with_name("manifest.json")
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["sha256"], revision.sha256)

    def test_closed_project_rejects_new_revision(self):
        with TemporaryDirectory() as root, patch.dict(os.environ, {"GAIDEN_WRITER_STORAGE_ROOT": root}):
            revision = import_manuscript_revision(self.project, self.upload())
            promote_canonical_manuscript(self.project, revision)
            close_writer_project(self.project)
            with self.assertRaises(ProjectClosedError):
                import_manuscript_revision(self.project, self.upload("later.md"))

    def test_hash_divergence_blocks_closure(self):
        with TemporaryDirectory() as root, patch.dict(os.environ, {"GAIDEN_WRITER_STORAGE_ROOT": root}):
            revision = import_manuscript_revision(self.project, self.upload())
            promote_canonical_manuscript(self.project, revision)
            Path(revision.storage_path).write_text("tampered\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                close_writer_project(self.project)
            self.project.refresh_from_db()
            self.assertEqual(self.project.status, StoryProject.Status.CANONICAL_READY)

    def test_revision_from_another_project_cannot_become_canonical(self):
        other = StoryProject.objects.create(title="Other book", chapter_count=1)
        with TemporaryDirectory() as root, patch.dict(os.environ, {"GAIDEN_WRITER_STORAGE_ROOT": root}):
            revision = import_manuscript_revision(other, self.upload("other.md"))
            with self.assertRaises(ValueError):
                promote_canonical_manuscript(self.project, revision)
            self.project.refresh_from_db()
            self.assertIsNone(self.project.canonical_manuscript_id)


class WriterIntakeBridgeTests(TestCase):
    def setUp(self):
        self.project = StoryProject.objects.create(
            title="Bridge Book",
            language="English (United Kingdom)",
            chapter_count=1,
        )
        Language.objects.create(code="en", name="English", native_name="English")
        Seal.objects.create(slug="rinobooks-bridge", name="RinoBooks")

    def test_closed_canonical_creates_one_final_md_edition_and_reuses_it(self):
        with TemporaryDirectory() as root, patch.dict(
            os.environ,
            {
                "GAIDEN_WRITER_STORAGE_ROOT": root,
                "GAIDEN_STORAGE_ROOT": root,
            },
        ):
            revision = import_manuscript_revision(
                self.project,
                SimpleUploadedFile("canonical.md", b"# Chapter 1\n\nApproved body."),
            )
            promote_canonical_manuscript(self.project, revision)
            close_writer_project(self.project)

            first = register_closed_writer_in_intake(
                project=self.project,
                author_name="Author Bridge",
                publisher="RinoBooks",
            )
            second = register_closed_writer_in_intake(
                project=self.project,
                author_name="Author Bridge",
                publisher="RinoBooks",
            )

            self.assertTrue(first.created)
            self.assertFalse(second.created)
            self.assertEqual(first.edition.id, second.edition.id)
            self.assertEqual(Edition.objects.filter(work=first.edition.work).count(), 1)
            self.assertEqual(first.working_path.read_bytes(), Path(revision.storage_path).read_bytes())
            self.assertEqual(first.working_sha256, revision.sha256)
            self.assertEqual(first.edition.raw_source_path, "")
            pipeline = EditionPipeline.objects.get(edition=first.edition)
            self.assertEqual(pipeline.current_stage, "FINAL_MD")
            self.assertIsNone(pipeline.raw_at)
            self.assertEqual(first.edition.work.source_provenance["origin"], "writer_canonical")


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
                "embedding_model": "local-test-embedding",
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
                number=1,
                direction="",
                script="",
                generated_text="",
                status=Chapter.Status.PLANNED,
                sessions=self.Related([]),
            )
            project = SimpleNamespace(
                sources=self.Related(sources),
                chapters=self.Related([chapter]),
                vector_index_path=str(index_path),
                character_bible="",
                supporting_characters_bible="",
                antagonist_bible="",
                scenario_bible="",
                world_bible="",
                story_direction="",
                story_outline="",
            )
            scope = {"selected": 2, "normalized": 2, "vectorized": 2, "failed": 0,
                     "all_normalized": True, "all_vectorized": True, "blockers": [],
                     "vectorization_available": False, "index": {"valid": True, "chunk_count": 18,
                                                                  "dimension": 1024, "reason": "Pronto"}}
            with patch("web.writer.services.dashboard._reference_scope_status", return_value=scope), patch.dict(os.environ, {
                "GAIDEN_LOCAL_EMBEDDING_MODEL": "local-test-embedding",
                "GAIDEN_LOCAL_MODEL": "local-test-model",
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
        project = SimpleNamespace(id=2, pk=2, status=StoryProject.Status.WRITING)
        dashboard = {
            "sources": {"all_vectorized": True, "vectorized": 9},
            "index": {"chunk_count": 803},
            "corpus": {"preparation_pending": 0, "vectorization_available": False},
            "vectorize_blockers": [],
        }
        with (
            patch("web.writer.views.get_object_or_404", return_value=project),
            patch("web.writer.views.build_project_dashboard", return_value=dashboard),
            patch("web.writer.views.vectorize_project_references") as vectorize_project_mock,
            patch("web.writer.views.messages.success") as success,
        ):
            response = self.client.post(
                reverse("writer:vectorize", args=[project.id]), {"confirm": "yes"}
            )

        self.assertEqual(response.status_code, 302)
        vectorize_project_mock.assert_not_called()
        self.assertIn("Vetorização concluída", success.call_args.args[1])

    def test_vectorization_from_sources_page_returns_to_sources(self):
        project = SimpleNamespace(id=2, pk=2, status=StoryProject.Status.WRITING)
        dashboard = {
            "sources": {"all_vectorized": True, "vectorized": 9},
            "index": {"chunk_count": 803},
            "corpus": {"preparation_pending": 0, "vectorization_available": False},
            "vectorize_blockers": [],
        }
        with (
            patch("web.writer.views.get_object_or_404", return_value=project),
            patch("web.writer.views.build_project_dashboard", return_value=dashboard),
            patch("web.writer.views.vectorize_project_references") as vectorize_project_mock,
            patch("web.writer.views.messages.success"),
        ):
            response = self.client.post(
                reverse("writer:vectorize", args=[project.id]),
                {"confirm": "yes", "next": "sources"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("writer:sources"))
        vectorize_project_mock.assert_not_called()

    def test_project_normalization_from_sources_page_returns_to_sources(self):
        project = SimpleNamespace(id=2, pk=2)
        with (
            patch("web.writer.views.get_object_or_404", return_value=project),
            patch("web.writer.views.prepare_project_corpus", return_value={"prepared": 1, "reused": 0, "pending": 0}),
            patch("web.writer.views.messages.info"),
        ):
            response = self.client.post(
                reverse("writer:prepare_corpus", args=[project.id]),
                {"next": "sources"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("writer:sources"))

    def test_completed_vectorization_remake_rebuilds_only_after_explicit_choice(self):
        project = SimpleNamespace(id=2, pk=2, status=StoryProject.Status.WRITING)
        dashboard = {
            "sources": {"all_vectorized": True, "vectorized": 9},
            "index": {"chunk_count": 803},
            "corpus": {"preparation_pending": 0, "vectorization_available": False},
            "vectorize_blockers": [],
        }
        with (
            patch("web.writer.views.get_object_or_404", return_value=project),
            patch("web.writer.views.build_project_dashboard", return_value=dashboard),
            patch("web.writer.views.vectorize_project_references", return_value={"vectorized": 9, "reused": 0, "pending": []}) as vectorize_project_mock,
            patch("web.writer.views.messages.success") as success,
        ):
            response = self.client.post(
                reverse("writer:vectorize", args=[project.id]), {"remake": "yes"}
            )

        self.assertEqual(response.status_code, 302)
        vectorize_project_mock.assert_called_once_with(project, force=True)
        self.assertIn("Revetorização concluída", success.call_args.args[1])

    def test_vectorization_keeps_preparation_as_separate_step(self):
        project = SimpleNamespace(id=2, pk=2, status=StoryProject.Status.WRITING)
        dashboard = {
            "sources": {"all_vectorized": False, "vectorized": 0},
            "index": {"chunk_count": 0},
            "corpus": {"preparation_pending": 2, "vectorization_available": False},
            "vectorize_blockers": ["Normalize as obras pendentes"],
        }
        with (
            patch("web.writer.views.get_object_or_404", return_value=project),
            patch("web.writer.views.build_project_dashboard", return_value=dashboard),
            patch("web.writer.views.vectorize_project_references") as vectorize,
            patch("web.writer.views.messages.warning") as warning,
        ):
            response = self.client.post(
                reverse("writer:vectorize", args=[project.id]), {"confirm": "yes"}
            )

        self.assertEqual(response.status_code, 302)
        vectorize.assert_not_called()
        self.assertIn("Normalize as obras pendentes", warning.call_args.args[1])


class SourcePageProjectActionsTests(TestCase):
    def test_sources_page_exposes_project_corpus_actions(self):
        language = Language.objects.create(code="en", name="English", native_name="English")
        author = Contributor.objects.create(name="Agatha Christie")
        work = EditorialWork.objects.create(
            code="agatha-test-work",
            title="Murder Test",
            original_language=language,
            author=author,
        )
        project = StoryProject.objects.create(title="Agatha project")
        ProjectReference.objects.create(project=project, intake_work=work)

        response = self.client.get(reverse("writer:sources"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Corpus dos projetos")
        self.assertContains(response, "Agatha project")
        self.assertContains(response, reverse("writer:prepare_corpus", args=[project.id]))
        self.assertContains(response, reverse("writer:vectorize", args=[project.id]))
        self.assertContains(response, 'name="next" value="sources"')


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


def fake_validated_session(chapter, number, engine, request, contract, previous):
    # These pre-existing tests cover Django orchestration; QA itself has separate tests.
    from gaiden.writer_engine.repair import ValidatedScene
    from gaiden.writer_engine.validation import ValidationResult
    return ValidatedScene(engine.create_chapter(request, top_k=chapter.retrieval_top_k),
        ValidationResult(True, 100, scope="mechanical_and_local_critic"), {}, "/test/audit")


class WriterQAIsolation:
    def setUp(self):
        super().setUp()
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        patcher = patch('web.writer.services.validation.writer_storage_root', return_value=Path(temporary.name))
        patcher.start()
        self.addCleanup(patcher.stop)


class ProjectAndChapterTests(WriterQAIsolation, TestCase):
    def _project(self, **overrides):
        values = {
            "title": "New Mystery",
            "character_bible": "Detective character facts",
            "supporting_characters_bible": "Supporting cast facts",
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

    @patch("web.writer.services.generation.generate_validated_session", new=fake_validated_session)
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
                    model="test/local",
                    source_chunk_ids=(f"source-{number}",),
                    source_scores=(0.9,),
                )

        engine_factory.return_value = FakeEngine()
        engine_factory.return_value.calls = []
        generate_chapter(chapter)
        chapter.refresh_from_db()
        self.assertEqual(chapter.status, Chapter.Status.GENERATION_COMPLETE)
        self.assertEqual(chapter.sessions.count(), 4)
        self.assertIn("Supporting characters bible:\nSupporting cast facts", engine_factory.return_value.calls[0].continuity)
        self.assertFalse(chapter.final_text)
        chapter.finalize()
        chapter.refresh_from_db()
        self.assertEqual(chapter.status, Chapter.Status.FINAL)
        self.assertIn("Original session 1", chapter.final_text)

    @patch("web.writer.services.generation.generate_validated_session", new=fake_validated_session)
    @patch("web.writer.services.generation._engine")
    def test_generation_can_stop_after_one_selected_session(self, engine_factory):
        project = self._project(chapter_count=1, vector_index_path="/runtime/index.jsonl")
        synchronize_chapters(project)
        chapter = project.chapters.get()
        chapter.direction = "Investigate the locked room"
        chapter.script = "Opening, clue, confrontation, resolution"
        chapter.save()
        engine_factory.return_value.create_chapter.return_value = GenerationResult(
            text=("First session. " * 220),
            model="test/local",
            source_chunk_ids=("source-1",),
            source_scores=(0.9,),
        )

        generate_chapter(chapter, session_numbers=[1])

        chapter.refresh_from_db()
        self.assertEqual(chapter.status, Chapter.Status.PLANNED)
        self.assertEqual(list(chapter.sessions.values_list("number", flat=True)), [1])
        self.assertTrue(chapter.sessions.get().qa_passed)

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


class WriterIndexPathTests(SimpleTestCase):
    def test_engine_loads_index_from_project_string_field(self):
        from web.writer.services.generation import _engine
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "index.jsonl"
            path.write_text(json.dumps({"schema_version": 1, "dimension": 2,
                "embedding_model": "test-model", "source_count": 0,
                "chunk_count": 0}) + "\n")
            chapter = SimpleNamespace(project=SimpleNamespace(vector_index_path=str(path)))
            with patch("web.writer.services.generation.LocalEmbeddingClient"), patch("web.writer.services.generation.writer_generator"):
                engine = _engine(chapter)
            self.assertEqual(engine.index.dimension, 2)


class WriterAccelerationGateTests(SimpleTestCase):
    def response(self, payload):
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = json.dumps(payload).encode()
        return response

    @patch("web.writer.services.runtime.urlopen")
    def test_gate_preloads_and_accepts_canonical_gpu_cpu_distribution(self, opener):
        from web.writer.services.runtime import require_optimal_acceleration
        opener.side_effect = [
            self.response({"done": True}),
            self.response({"models": [{
                "name": "writer-model", "size": 1000, "size_vram": 660
            }]}),
        ]

        status = require_optimal_acceleration(
            base_url="http://127.0.0.1:8001/v1", model="writer-model"
        )

        self.assertEqual(status.gpu_percent, 66.0)
        self.assertEqual(status.cpu_percent, 34.0)
        self.assertEqual(opener.call_count, 2)

    @patch("web.writer.services.runtime.urlopen")
    def test_gate_rejects_cpu_heavy_distribution_before_generation(self, opener):
        from web.writer.services.runtime import require_optimal_acceleration
        opener.side_effect = [
            self.response({"done": True}),
            self.response({"models": [{
                "name": "writer-model", "size": 1000, "size_vram": 500
            }]}),
        ]

        with self.assertRaisesMessage(RuntimeError, "50.0% GPU / 50.0% CPU"):
            require_optimal_acceleration(
                base_url="http://127.0.0.1:8001/v1", model="writer-model"
            )


class SessionSceneTests(WriterQAIsolation, TestCase):
    _project = ProjectAndChapterTests._project

    def test_scene_validation_rejects_missing_sessions_and_blank_scenes(self):
        from web.writer.services.scenes import validate_session_scenes
        for value in ([['Opening']], [['Opening'], [' ']], 'invalid'):
            with self.assertRaises(ValueError):
                validate_session_scenes(value, 2)
        self.assertEqual(validate_session_scenes([[' First ', 'Second']], 1), [['First', 'Second']])

    def test_form_persists_scenes_without_general_direction(self):
        from web.writer.forms import ChapterForm
        chapter = Chapter.objects.create(project=self._project(), number=1)
        data = {'title': 'Scenes', 'direction': '', 'script': '', 'session_count': 2,
                'target_words': 1600, 'retrieval_top_k': 8,
                'session_scenes': json.dumps([['Arrival', 'Discovery'], ['Confrontation']])}
        form = ChapterForm(data, instance=chapter)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        chapter.refresh_from_db()
        self.assertEqual(chapter.session_scenes[0], ['Arrival', 'Discovery'])
        ChapterSession.objects.create(chapter=chapter, number=1, content='Preserved draft')
        data['session_scenes'] = json.dumps([['Changed'], ['Confrontation']])
        form = ChapterForm(data, instance=chapter)
        self.assertFalse(form.is_valid())
        chapter.refresh_from_db()
        self.assertEqual(chapter.session_scenes[0], ['Arrival', 'Discovery'])

    @patch('web.writer.services.generation.generate_validated_session', new=fake_validated_session)
    @patch('web.writer.services.generation._engine')
    def test_generation_assigns_only_current_scenes_and_resumes_without_repetition(self, factory):
        chapter = Chapter.objects.create(project=self._project(), number=1, session_count=2,
                                         session_scenes=[['Arrival', 'Discovery'], ['Confrontation']],
                                         direction='Old full chapter direction', script='Old full chapter script')
        factory.return_value.create_chapter.return_value = GenerationResult(
            text='Generated text', model='test', source_chunk_ids=(), source_scores=())
        generate_chapter(chapter)
        calls = factory.return_value.create_chapter.call_args_list
        self.assertIn('1. Arrival\n2. Discovery', calls[0].args[0].brief)
        self.assertNotIn('Confrontation', calls[0].args[0].brief)
        self.assertNotIn('Old full chapter', calls[0].args[0].brief)
        self.assertIn('1. Confrontation', calls[1].args[0].brief)
        self.assertNotIn('Arrival', calls[1].args[0].brief)
        self.assertIn('Generated text', calls[1].args[0].continuity)
        self.assertEqual(chapter.sessions.get(number=1).generation_parameters['scenes'], ['Arrival', 'Discovery'])
        generate_chapter(chapter)
        self.assertEqual(factory.return_value.create_chapter.call_count, 2)

    @patch('web.writer.services.generation._engine')
    def test_invalid_scene_plan_stops_before_model_call(self, factory):
        chapter = Chapter.objects.create(project=self._project(), number=1, session_count=2,
                                         session_scenes=[['Only first session']])
        with self.assertRaises(ValueError):
            generate_chapter(chapter)
        factory.assert_not_called()

    def test_scene_editor_and_plan_are_visible_and_escaped(self):
        chapter = Chapter.objects.create(project=self._project(), number=1,
                                         session_scenes=[['<script>alert(1)</script>']])
        response = self.client.get(reverse('writer:chapter_edit', args=[chapter.pk]))
        self.assertContains(response, 'Cenas por sessão')
        self.assertContains(response, '+ Adicionar cena')
        self.assertContains(response, '− Remover cena')
        response = self.client.get(reverse('writer:chapter_detail', args=[chapter.pk]))
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')

    def test_can_plan_remaining_sessions_after_legacy_first_session(self):
        from web.writer.forms import ChapterForm
        chapter = Chapter.objects.create(project=self._project(), number=1)
        session = ChapterSession.objects.create(chapter=chapter, number=1,
            status=ChapterSession.Status.COMPLETE, content='Original first session')
        data = {'title': '', 'direction': '', 'script': '', 'session_count': 4,
                'target_words': 2500, 'retrieval_top_k': 8,
                'session_scenes': json.dumps([[], ['Second'], ['Third'], ['Fourth']])}
        form = ChapterForm(data, instance=chapter)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        chapter.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(chapter.session_scenes, [[], ['Second'], ['Third'], ['Fourth']])
        self.assertEqual(session.content, 'Original first session')
        data['session_scenes'] = json.dumps([['Rewrite first'], ['Second'], ['Third'], ['Fourth']])
        self.assertFalse(ChapterForm(data, instance=chapter).is_valid())


class GenerationGuardrailTests(WriterQAIsolation, TestCase):
    _project = ProjectAndChapterTests._project

    def chapter(self):
        return Chapter.objects.create(project=self._project(), number=1,
            session_count=2, session_scenes=[['Arrival'], ['Investigation']])

    @patch('web.writer.services.generation._engine')
    def test_stale_duplicate_is_blocked_before_model_call(self, factory):
        chapter = self.chapter()
        Chapter.objects.filter(pk=chapter.pk).update(status=Chapter.Status.GENERATING)
        with self.assertRaisesMessage(ValueError, 'já está em geração'):
            generate_chapter(chapter)
        factory.assert_not_called()
        chapter.refresh_from_db()
        self.assertEqual(chapter.status, Chapter.Status.GENERATING)

    @patch('web.writer.services.generation._engine')
    def test_repetition_stops_after_three_attempts_and_preserves_completed_session(self, factory):
        chapter = self.chapter()
        text = ' '.join(f'word{i}' for i in range(90))
        first = ChapterSession.objects.create(chapter=chapter, number=1,
            status=ChapterSession.Status.COMPLETE, content=text)
        factory.return_value.prepare_chapter.return_value = SimpleNamespace(user="Original prompt", sources=())
        factory.return_value.generate_candidate.return_value = GenerationResult(
            text=text, model='test', source_chunk_ids=(), source_scores=())
        with self.assertRaisesMessage(RuntimeError, 'failed after 3 attempts'):
            generate_chapter(chapter)
        self.assertEqual(factory.return_value.generate_candidate.call_count, 3)
        first.refresh_from_db()
        self.assertEqual(first.content, text)
        self.assertEqual(chapter.sessions.count(), 1)
        chapter.refresh_from_db()
        self.assertEqual(chapter.status, Chapter.Status.FAILED)

    @patch('web.writer.services.generation._engine')
    def test_rejected_saved_session_blocks_before_gpu_work(self, factory):
        chapter = self.chapter()
        ChapterSession.objects.create(chapter=chapter, number=1,
            status=ChapterSession.Status.FAILED, content='Preserved rejected draft')
        with self.assertRaisesMessage(ValueError, 'revisão manual'):
            generate_chapter(chapter)
        factory.assert_not_called()
        self.assertEqual(chapter.sessions.get().content, 'Preserved rejected draft')

    @patch('web.writer.services.generation._engine')
    def test_engine_failure_releases_claim_without_retry(self, factory):
        chapter = self.chapter()
        factory.side_effect = RuntimeError('offline')
        with self.assertRaisesMessage(RuntimeError, 'offline'):
            generate_chapter(chapter)
        factory.assert_called_once()
        chapter.refresh_from_db()
        self.assertEqual(chapter.status, Chapter.Status.FAILED)

    def test_model_clients_reject_cloud_model_names(self):
        from gaiden.infrastructure.local_models import local_model_name
        with self.assertRaises(ValueError):
            local_model_name("gpt-5")


# Manuscript finalization: included in the protected web.writer.tests suite.
class MergeSafetyTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"GAIDEN_WRITER_STORAGE_ROOT": self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.project = StoryProject.objects.create(title="Book", chapter_count=1)
        Chapter.objects.create(project=self.project, number=1, title="Opening",
                               status=Chapter.Status.FINAL, final_text="Final chapter text.")

    def test_closed_project_cannot_create_merge(self):
        self.project.status = StoryProject.Status.CLOSED
        self.project.save()
        with self.assertRaises(ProjectClosedError):
            save_merged_manuscript(self.project)
        self.assertFalse(self.project.manuscript_revisions.exists())

    def test_merge_reuse_checks_physical_hash(self):
        revision = save_merged_manuscript(self.project)
        Path(revision.storage_path).write_text("tampered", encoding="utf-8")
        with self.assertRaises(ValueError):
            save_merged_manuscript(self.project)
        self.assertEqual(self.project.manuscript_revisions.count(), 1)

    def test_merge_never_overwrites_an_existing_next_path(self):
        path = Path(self.temp.name) / "projects" / str(self.project.pk) / "manuscripts/manuscript_v001.md"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"operator file")
        with self.assertRaises(ValueError):
            save_merged_manuscript(self.project)
        self.assertEqual(path.read_bytes(), b"operator file")

    def test_merge_remains_versioned_and_idempotent(self):
        first = save_merged_manuscript(self.project)
        same = save_merged_manuscript(self.project)
        self.assertEqual(first.pk, same.pk)
        Chapter.objects.filter(project=self.project).update(final_text="A new reviewed chapter.")
        second = save_merged_manuscript(self.project)
        self.assertEqual(second.version, first.version + 1)
        self.assertEqual(first.sha256, hashlib.sha256(Path(first.storage_path).read_bytes()).hexdigest())


class FinalizationTests(TestCase):
    setUp = MergeSafetyTests.setUp

    def imported(self, text=b"# Chapter 1\n\nReviewed final text."):
        return import_manuscript_revision(self.project, SimpleUploadedFile("reviewed.md", text))

    def test_export_requires_a_saved_merge(self):
        with self.assertRaisesMessage(ValueError, "salve um merge"):
            export_merge(self.project)
        self.assertFalse((Path(self.temp.name) / "projects" / str(self.project.pk) / "return").exists())

    def test_export_preserves_bytes_and_every_prior_version(self):
        merge = save_merged_manuscript(self.project)
        first, second = export_merge(self.project), export_merge(self.project)
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(first.path.read_bytes(), second.path.read_bytes())
        self.assertEqual(first.data, Path(merge.storage_path).read_bytes())
        metadata = json.loads(second.path.with_suffix(".json").read_text())
        self.assertEqual(metadata["sha256"], merge.sha256)
        self.assertEqual(metadata["export_version"], 2)
        self.assertEqual(first.path.parent.name, "return")

    def test_export_uses_latest_merge_even_with_stale_pointer_and_saved_final(self):
        first = save_merged_manuscript(self.project)
        final = self.imported()
        save_final(self.project, final)
        Chapter.objects.filter(project=self.project).update(final_text="New merge only.")
        latest = save_merged_manuscript(self.project)
        StoryProject.objects.filter(pk=self.project.pk).update(manuscript_merge_revision=first)
        self.assertEqual(export_merge(self.project).revision.pk, latest.pk)
        self.project.refresh_from_db()
        self.assertEqual(edition_source(self.project).pk, final.pk)

    def test_unsaved_import_does_not_replace_merge(self):
        merge = save_merged_manuscript(self.project)
        self.imported()
        self.project.refresh_from_db()
        self.assertEqual(edition_source(self.project).pk, merge.pk)

    def test_saved_final_wins_even_when_chapters_are_no_longer_final(self):
        final = self.imported()
        save_final(self.project, final)
        Chapter.objects.filter(project=self.project).update(status=Chapter.Status.PLANNED)
        frozen, used_merge = prepare_edition_source(self.project)
        self.assertFalse(used_merge)
        self.assertEqual(frozen.canonical_manuscript_id, final.pk)
        self.assertEqual(frozen.status, StoryProject.Status.CLOSED)

    def test_final_cannot_be_demoted_to_merge_by_legacy_canonical_route(self):
        merge = save_merged_manuscript(self.project)
        final = self.imported()
        save_final(self.project, final)
        with self.assertRaisesMessage(ValueError, "Final Version"):
            promote_canonical_manuscript(self.project, merge)
        self.project.refresh_from_db()
        self.assertEqual(self.project.canonical_manuscript_id, final.pk)

    def test_tampered_final_blocks_handoff_without_chapter_fallback(self):
        final = self.imported()
        save_final(self.project, final)
        save_merged_manuscript(self.project)
        Path(final.storage_path).write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            prepare_edition_source(self.project)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, StoryProject.Status.CANONICAL_READY)
        self.assertEqual(self.project.manuscript_revisions.count(), 2)

    def test_chapter_fallback_only_when_neither_final_nor_merge_exists(self):
        frozen, used_merge = prepare_edition_source(self.project)
        self.assertTrue(used_merge)
        self.assertEqual(frozen.status, StoryProject.Status.CLOSED)
        self.assertEqual(self.project.manuscript_revisions.count(), 1)
        repeated, _ = prepare_edition_source(self.project)
        self.assertEqual(repeated.canonical_manuscript_id, frozen.canonical_manuscript_id)
        self.assertEqual(self.project.manuscript_revisions.count(), 1)

    def test_incomplete_chapters_cannot_be_used_as_fallback(self):
        Chapter.objects.filter(project=self.project).update(status=Chapter.Status.PLANNED)
        with self.assertRaises(ValueError):
            prepare_edition_source(self.project)
        self.assertFalse(self.project.manuscript_revisions.exists())

    def test_save_final_checks_selection_ownership_and_hash(self):
        merge = save_merged_manuscript(self.project)
        with self.assertRaises(ValueError):
            save_final(self.project, merge)
        other = StoryProject.objects.create(title="Other")
        revision = import_manuscript_revision(other, SimpleUploadedFile("other.txt", b"Other final"))
        with self.assertRaises(ValueError):
            save_final(self.project, revision)
        final = self.imported()
        Path(final.storage_path).write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            save_final(self.project, final)

    def test_progressive_controls_and_selected_filename_are_visible(self):
        response = self.client.get(reverse("writer:project_detail", args=[self.project.pk]))
        self.assertContains(response, '>EXPORT MERGE</button>')
        self.assertContains(response, 'disabled>EXPORT MERGE')
        self.assertContains(response, 'disabled>SAVE FINAL')
        self.assertContains(response, 'GOOGLE DRIVE')
        self.assertContains(response, 'BROWSE COMPUTER')
        merge = save_merged_manuscript(self.project)
        response = self.client.get(reverse("writer:project_detail", args=[self.project.pk]))
        self.assertNotContains(response, 'disabled>EXPORT MERGE')
        self.assertContains(response, 'No imported Final Version. The latest Merge will be used.')
        imported = self.imported()
        response = self.client.get(reverse("writer:project_detail", args=[self.project.pk]), {"selected_final": imported.pk})
        self.assertContains(response, 'Selected: reviewed.md')
        self.assertNotContains(response, 'disabled>SAVE FINAL')
        response = self.client.post(reverse("writer:save_final", args=[self.project.pk]), {"revision_id": imported.pk})
        self.assertEqual(response.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.canonical_manuscript_id, imported.pk)
        self.assertNotEqual(imported.pk, merge.pk)

    def test_export_is_post_only_and_returns_versioned_download(self):
        url = reverse("writer:export_merge", args=[self.project.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        merge = save_merged_manuscript(self.project)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, Path(merge.storage_path).read_bytes())
        self.assertIn('attachment;', response["Content-Disposition"])

    def test_local_upload_remains_candidate_until_save_final(self):
        url = reverse("writer:import_manuscript", args=[self.project.pk])
        response = self.client.post(url, {"manuscript": SimpleUploadedFile("reviewed.md", b"Final file.")})
        self.assertEqual(response.status_code, 302)
        self.assertIn('selected_final=', response.url)
        self.project.refresh_from_db()
        self.assertIsNone(self.project.canonical_manuscript)

    def test_whitespace_only_upload_is_rejected(self):
        with self.assertRaises(ValueError):
            self.imported(b"  \n  ")

    def test_go_to_edition_reuses_intake_and_opens_frontmatter(self):
        from editorial.models import Language, Seal
        Language.objects.create(code="en", name="English", native_name="English")
        Seal.objects.create(name="RinoBooks", slug="rinobooks-test")
        self.project.language = "English (United Kingdom)"
        self.project.save()
        merge = save_merged_manuscript(self.project)
        with patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": self.temp.name}):
            response = self.client.post(reverse("writer:proceed_to_intake", args=[self.project.pk]),
                                        {"author_name": "Test Author", "publisher": "RinoBooks"})
            self.assertEqual(response.status_code, 302)
            self.assertIn('/frontmatter/', response.url)
            self.assertIn('edition_source=writer', response.url)
            self.assertIn('workflow_edition_id=', response.url)
            self.project.refresh_from_db()
            self.assertEqual(self.project.canonical_manuscript_id, merge.pk)
            self.assertEqual(self.project.status, StoryProject.Status.CLOSED)
            # Existing frontmatter template and contextual navigation remain in use.
            frontmatter = self.client.get(response.url)
            self.assertEqual(frontmatter.status_code, 200)
            self.assertTemplateUsed(frontmatter, 'editorial/frontmatter_form.html')
            self.assertEqual(frontmatter.context['edition_source'], 'writer')
            self.assertContains(frontmatter, 'FIGURES')
            self.assertNotContains(frontmatter, 'Normalize')
            self.assertNotContains(frontmatter, 'Headings Cleaning')

    def test_docx_upload_reuses_existing_reader_with_bounded_archive(self):
        import io
        from docx import Document
        document = Document()
        document.add_paragraph('Final DOCX paragraph.')
        stream = io.BytesIO()
        document.save(stream)
        revision = import_manuscript_revision(self.project, SimpleUploadedFile('final.docx', stream.getvalue()))
        self.assertIn('Final DOCX paragraph.', Path(revision.storage_path).read_text())

    def test_invalid_docx_is_rejected_without_revision(self):
        with self.assertRaises(ValueError):
            import_manuscript_revision(self.project, SimpleUploadedFile('bad.docx', b'not a zip'))
        self.assertFalse(self.project.manuscript_revisions.exists())


class DriveFinalizationTests(TestCase):
    setUp = MergeSafetyTests.setUp

    def metadata(self, **changes):
        result = {"id": "drive_file_123456", "name": "reviewed.md", "mimeType": "text/markdown",
                  "version": "3", "modifiedTime": "2026-10-01T12:00:00Z", "size": "30", "trashed": False}
        result.update(changes)
        return result

    @patch("web.writer.services.manuscript_drive._download", return_value=b"# Chapter 1\n\nFinal from Drive.\n")
    @patch("web.writer.services.manuscript_drive._drive")
    def test_drive_import_records_provenance_and_reuses_same_file_version(self, factory, download):
        factory.return_value.files.return_value.get.return_value.execute.return_value = self.metadata()
        first = import_drive_final(self.project, 'https://drive.google.com/file/d/drive_file_123456/view')
        again = import_drive_final(self.project, 'drive_file_123456')
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(self.project.manuscript_revisions.count(), 1)
        record = json.loads(Path(first.storage_path).with_suffix('.source.json').read_text())
        self.assertEqual(record['drive_file_id'], 'drive_file_123456')
        self.assertEqual(record['drive_version'], '3')
        self.assertEqual(record['sha256'], first.sha256)
        self.project.refresh_from_db()
        self.assertIsNone(self.project.canonical_manuscript)
        self.assertEqual(self.project.manuscript_drive_id, 'drive_file_123456')
        factory.return_value.files.return_value.create.assert_not_called()
        factory.return_value.files.return_value.update.assert_not_called()

    @patch("web.writer.services.manuscript_drive._download", return_value=b"Drive final")
    @patch("web.writer.services.manuscript_drive._drive")
    def test_changing_drive_version_is_rejected_without_local_write(self, factory, download):
        factory.return_value.files.return_value.get.return_value.execute.side_effect = [self.metadata(), self.metadata(version='4')]
        with self.assertRaises(ValueError):
            import_drive_final(self.project, 'drive_file_123456')
        self.assertFalse(self.project.manuscript_revisions.exists())

    @patch("web.writer.services.manuscript_drive._download")
    @patch("web.writer.services.manuscript_drive._drive")
    def test_unsupported_file_and_trashed_file_are_not_downloaded(self, factory, download):
        for metadata in (self.metadata(name='bad.exe', mimeType='application/octet-stream'), self.metadata(trashed=True)):
            factory.return_value.files.return_value.get.return_value.execute.return_value = metadata
            with self.assertRaises(ValueError):
                import_drive_final(self.project, 'drive_file_123456')
        download.assert_not_called()

    @patch("web.writer.services.manuscript_drive._drive")
    def test_project_folder_picker_lists_without_downloading(self, factory):
        self.project.drive_project_folder_id = 'drive_folder_12345'
        factory.return_value.files.return_value.list.return_value.execute.return_value = {
            'files': [self.metadata(), self.metadata(name='ignore.pdf', mimeType='application/pdf')],
            'nextPageToken': 'next',
        }
        files, token = list_final_files(self.project)
        self.assertEqual([file['name'] for file in files], ['reviewed.md'])
        self.assertEqual(token, 'next')
        factory.return_value.files.return_value.get_media.assert_not_called()

    def test_download_buffer_enforces_limit_without_creating_a_revision(self):
        with patch('web.writer.services.manuscript_drive.MAX_MANUSCRIPT_BYTES', 3):
            stream = _BoundedBuffer()
            stream.write(b'123')
            with self.assertRaises(ValueError):
                stream.write(b'4')

    @patch("web.writer.services.manuscript_drive._drive")
    def test_drive_picker_and_upload_route_reuse_existing_importer(self, factory):
        self.project.drive_project_folder_id = 'drive_folder_12345'
        self.project.save()
        factory.return_value.files.return_value.list.return_value.execute.return_value = {'files': [self.metadata()]}
        response = self.client.get(reverse('writer:import_final_drive', args=[self.project.pk]))
        self.assertContains(response, 'reviewed.md')
        self.assertContains(response, 'Selecionar e importar')
        factory.return_value.files.return_value.get.return_value.execute.return_value = self.metadata()
        with patch('web.writer.services.manuscript_drive._download', return_value=b'Reviewed Drive file.'):
            response = self.client.post(reverse('writer:import_final_drive', args=[self.project.pk]),
                                        {'file_id': 'drive_file_123456'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('selected_final=', response.url)

    @patch("web.writer.services.manuscript_drive._download", return_value=b"Reviewed Drive file.")
    @patch("web.writer.services.manuscript_drive._drive")
    def test_failed_provenance_write_preserves_operator_file_and_rolls_back_revision(self, factory, download):
        factory.return_value.files.return_value.get.return_value.execute.return_value = self.metadata()
        path = Path(self.temp.name) / 'projects' / str(self.project.pk) / 'manuscripts/manuscript_v001.source.json'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'operator provenance')
        with self.assertRaises(ValueError):
            import_drive_final(self.project, 'drive_file_123456')
        self.assertEqual(path.read_bytes(), b'operator provenance')
        self.assertFalse(path.with_name('manuscript_v001.md').exists())
        self.assertFalse(self.project.manuscript_revisions.exists())

    @patch('googleapiclient.http.MediaIoBaseDownload')
    def test_native_google_doc_uses_read_only_text_export(self, downloader):
        from web.writer.services.manuscript_drive import _download
        drive = MagicMock()
        downloader.return_value.next_chunk.return_value = (None, True)
        metadata = self.metadata(mimeType='application/vnd.google-apps.document')
        _download(drive, metadata)
        drive.files.return_value.export_media.assert_called_once_with(fileId=metadata['id'], mimeType='text/plain')
        drive.files.return_value.get_media.assert_not_called()

    @patch('googleapiclient.http.MediaIoBaseDownload')
    def test_oversize_file_stops_before_network_download(self, downloader):
        from web.writer.services.manuscript_drive import _download
        with self.assertRaises(ValueError):
            _download(MagicMock(), self.metadata(size=str(51 * 1024 * 1024)))
        downloader.assert_not_called()


class EditionWorkflowTests(SimpleTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / 'BOOK.MD_FINAL'
        self.source.write_bytes(b'# Chapter 1\n\nFrozen manuscript.')
        self.edition = SimpleNamespace(pk=7, id=7, language=SimpleNamespace(code='en'), work=SimpleNamespace(
            code='writer-test', source_provenance={'origin': 'writer_canonical', 'intake_book_code': 'book_0007', 'working_manuscript_path': str(self.source),
                               'source_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()}))
        root_patch = patch('pipeline.services.writer_edition_workflow.storage.storage_root', return_value=root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    @patch('pipeline.services.writer_edition_workflow.EditionRenderer')
    @patch('pipeline.services.writer_edition_workflow.kdp_mode.build_merged_kdp_source')
    @patch('pipeline.services.writer_edition_workflow.kdp_mode.build_frontmatter_files')
    def test_assembly_reuses_renderer_without_text_transformation(self, frontmatter, legacy_merge, renderer):
        from pipeline.services.writer_edition_workflow import assemble_writer_edition
        original = self.source.read_bytes()
        assemble_writer_edition(self.edition)
        renderer.assert_called_once_with(self.edition, source_path=self.source)
        legacy_merge.assert_not_called()
        self.assertEqual(self.source.read_bytes(), original)

    @patch('pipeline.services.writer_edition_workflow.import_final_epub')
    @patch('pipeline.services.writer_edition_workflow.EditionRenderer')
    def test_unapproved_or_changed_edition_cannot_generate(self, factory, importer):
        from pipeline.services.writer_edition_workflow import generate_writer_epub
        factory.return_value.preview_is_approved.side_effect = [False]
        with self.assertRaises(ValueError):
            generate_writer_epub(self.edition)
        factory.return_value.preview_is_approved.side_effect = [True, False]
        with self.assertRaises(ValueError):
            generate_writer_epub(self.edition)
        factory.return_value.build_epub.assert_not_called()
        importer.assert_not_called()

    @patch('pipeline.services.writer_edition_workflow.import_final_epub')
    @patch('pipeline.services.writer_edition_workflow.EditionRenderer')
    def test_epub_generation_reuses_existing_epubcheck_gate(self, factory, importer):
        from pipeline.services.writer_edition_workflow import generate_writer_epub
        output = Path(self.temp.name) / 'BOOK.epub'
        output.write_bytes(b'packaged-epub-fixture')
        factory.return_value.preview_is_approved.return_value = True
        factory.return_value.build_epub.return_value = output
        generate_writer_epub(self.edition)
        kwargs = importer.call_args.kwargs
        self.assertEqual(kwargs['official_body_path'], self.source)
        self.assertTrue(kwargs['approved'])
        self.assertNotIn('skip_epubcheck_for_tests', kwargs)
        self.assertEqual(kwargs['expected_sha256'], hashlib.sha256(output.read_bytes()).hexdigest())

    def test_damaged_canonical_is_rejected(self):
        from pipeline.services.writer_edition_workflow import canonical_source
        self.source.write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            canonical_source(self.edition)

    def test_direct_frontmatter_url_recovers_writer_navigation_from_provenance(self):
        from editorial.views import _post_intake_return_context
        url, query = _post_intake_return_context(RequestFactory().get('/'), self.edition.work.code, self.edition)
        self.assertEqual(url, reverse('writer_intake_assets', args=[7]))
        self.assertIn('edition_source=writer', query)

    @patch('editorial.views.messages')
    @patch('editorial.views.get_object_or_404')
    @patch('editorial.views.EditionRenderer')
    def test_writer_approval_records_time_and_version_and_preserves_context(self, factory, find, messages):
        from editorial.views import premium_epub_approve
        find.return_value = self.edition
        factory.return_value.approve_preview.return_value = SimpleNamespace(fingerprint='edition-fingerprint')
        factory.return_value._load_state.return_value = {}
        response = premium_epub_approve(RequestFactory().post('/'), 7)
        state = factory.return_value._write_state.call_args.args[0]
        self.assertEqual(state['edition_version_id'], 'edition-fingerprint')
        self.assertIn('approved_at', state)
        self.assertIn('edition_source=writer', response.url)

    @patch('editorial.views.messages')
    @patch('editorial.views.get_object_or_404')
    @patch('editorial.views.build_book_preview_context')
    def test_damaged_writer_source_cannot_fall_back_to_another_preview(self, build_preview, find, messages):
        from editorial.views import premium_epub_preview
        find.return_value = self.edition
        self.source.write_bytes(b'tampered')
        response = premium_epub_preview(RequestFactory().get('/'), 7)
        self.assertEqual(response.status_code, 302)
        build_preview.assert_not_called()
