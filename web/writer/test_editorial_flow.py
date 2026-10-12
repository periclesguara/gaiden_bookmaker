import json
import os
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.test import TestCase
from django.urls import reverse

from editorial.models import Contributor, Edition, EditionBuild, Language, Seal, Work
from gaiden.writer_engine.corpus import chunk_source
from gaiden.writer_engine.index import VectorIndex
from .models import Chapter, ChapterNarrativeSummary, ProjectReference, SourceVectorIndex, StoryProject
from .services.corpus import prepare_project_corpus
from .services.dashboard import build_project_dashboard
from .services.references import vectorize_project_references
from .services.source_retrieval import retrieve_source_rag
from .services.context_budget import allocate_context, ContextBudgetExceeded
from .services.chapter_closure import close_chapter
from .services.contracts import build_narrative_contract
from .services.execution_order import chapter_is_sequentially_eligible


class EditorialFlowTests(TestCase):
    def setUp(self):
        language = Language.objects.create(code="flow-en", name="English")
        self.language = language
        self.doyle = Contributor.objects.create(name="Arthur Conan Doyle")
        self.christie = Contributor.objects.create(name="Agatha Christie")
        self.holmes = Work.objects.create(code="flow-holmes", title="Holmes", author=self.doyle, original_language=language)
        self.ackroyd = Work.objects.create(code="flow-ackroyd", title="The Murder of Roger Ackroyd", author=self.christie, original_language=language)
        self.project = StoryProject.objects.create(title="Mystery", language="en", reference_bible="Keep guide")
        # This suite exercises the preserved ProjectReference workflow.
        config = self.project.canonical_corpus
        config.backend = "LEGACY_WRITER"
        config.save(update_fields=("backend",))

    def chapter(self, number, **kwargs):
        return Chapter.objects.create(project=self.project, number=number, direction="Investigate", script="Find the clue", **kwargs)

    def test_search_by_title_or_author_without_listing_entire_catalog(self):
        url = reverse("writer:work_catalog")
        self.assertEqual(self.client.get(url).json()["results"], [])
        for query in ("Roger Ackroyd", "Agatha"):
            rows = self.client.get(url, {"q": query}).json()["results"]
            self.assertEqual([row["work_id"] for row in rows], [self.ackroyd.pk])

    def test_corpus_save_is_additive_idempotent_and_does_not_edit_project(self):
        existing = ProjectReference.objects.create(project=self.project, intake_work=self.holmes, roles=["CANON"], notes="Preserve")
        url = reverse("writer:project_references", args=[self.project.pk])
        for _ in range(2):
            response = self.client.post(url, {"work_ids": [self.ackroyd.pk]})
            self.assertRedirects(response, reverse("writer:project_detail", args=[self.project.pk]), fetch_redirect_response=False)
        self.assertEqual(self.project.references.count(), 2)
        existing.refresh_from_db()
        self.assertEqual(existing.notes, "Preserve")
        self.project.refresh_from_db()
        self.assertEqual(self.project.reference_bible, "Keep guide")

    def test_explicit_clear_removes_only_project_link_and_invalid_save_is_atomic(self):
        ProjectReference.objects.create(project=self.project, intake_work=self.holmes)
        url = reverse("writer:project_references", args=[self.project.pk])
        self.client.post(url, {"work_ids": [999999], "remove_work_ids": [self.holmes.pk]})
        self.assertEqual(self.project.references.count(), 1)
        self.client.post(url, {"work_ids": [self.ackroyd.pk], "remove_work_ids": [self.holmes.pk]})
        self.assertEqual(list(self.project.references.values_list("intake_work_id", flat=True)), [self.ackroyd.pk])
        self.assertTrue(Work.objects.filter(pk=self.holmes.pk).exists())

    def test_panel_has_one_compact_corpus_and_details_hide_technical_pending_list(self):
        ProjectReference.objects.create(project=self.project, intake_work=self.ackroyd)
        response = self.client.get(reverse("writer:project_detail", args=[self.project.pk]))
        self.assertContains(response, "Corpus do Projeto")
        self.assertContains(response, "Selecionar obras")
        self.assertNotContains(response, "Corpus e índice RAG")
        self.assertNotContains(response, "Configuração das referências selecionadas")
        self.assertNotContains(response, 'class="source-list"')
        self.assertContains(response, 'id="corpus-details"')

    def test_all_previous_chapters_need_final_state_and_summary(self):
        first = self.chapter(1, generated_text="Unfinished", status=Chapter.Status.OUTPUT_IMPORTED)
        second = self.chapter(2, generated_text="Done", final_text="Done", status=Chapter.Status.FINAL)
        ChapterNarrativeSummary.objects.create(project=self.project, chapter=second, short_summary="Second facts")
        third = self.chapter(3)
        self.assertFalse(chapter_is_sequentially_eligible(third)[0])
        first.status = Chapter.Status.FINAL
        first.final_text = first.generated_text
        first.save()
        ChapterNarrativeSummary.objects.create(project=self.project, chapter=first, short_summary="First facts")
        self.assertTrue(chapter_is_sequentially_eligible(third)[0])

    def test_raw_preparation_and_vectors_are_shared_across_projects_without_epub_build(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": temporary}):
            path = Path(temporary) / "raw.txt"
            path.write_text("# CHAPTER 1\n\n" + "Holmes examines the telegram. " * 90, encoding="utf-8")
            seal = Seal.objects.create(slug="flow-test", name="Test")
            Edition.objects.create(work=self.holmes, language=self.language, seal=seal, raw_source_path=str(path))
            reference = ProjectReference.objects.create(project=self.project, intake_work=self.holmes)
            result = prepare_project_corpus(self.project)
            self.assertEqual(result, {"prepared": 1, "reused": 0, "pending": 0})
            self.assertFalse(EditionBuild.objects.exists())
            original = path.read_bytes()
            embedder = Mock(model="offline-test")
            embedder.embed.side_effect = lambda texts: [[1.0, 0.5] for _ in texts]
            with patch("web.writer.services.references.embedding_client", return_value=embedder):
                first = vectorize_project_references(self.project)
                other = StoryProject.objects.create(title="Other")
                ProjectReference.objects.create(project=other, intake_work=self.holmes)
                reused = vectorize_project_references(other)
            self.assertEqual(first["vectorized"], 1)
            self.assertEqual(reused["reused"], 1)
            embedder.embed.assert_called_once()
            self.assertEqual(SourceVectorIndex.objects.count(), 1)
            self.assertEqual(prepare_project_corpus(other)["reused"], 1)
            self.assertEqual(path.read_bytes(), original)
            for project in (self.project, other):
                self.assertTrue(build_project_dashboard(project, probe_models=False)["corpus"]["all_vectorized"])
            reference.refresh_from_db()
            self.assertIsNone(reference.source_id)
            chapter = self.chapter(1, title="Holmes and the telegram")
            self.assertEqual(retrieve_source_rag(self.project, chapter)[0]["canonical_work_id"], self.holmes.pk)

    def test_author_batch_includes_unloaded_pages(self):
        Work.objects.bulk_create([Work(code=f"flow-page-{number}", title=f"Page {number}", author=self.christie,
                                      original_language=self.language) for number in range(24)])
        data = self.client.get(reverse("writer:work_catalog"), {"author": self.christie.pk, "all": "1"}).json()
        self.assertEqual(len(data["work_ids"]), 25)

    def test_corrupted_index_is_not_ready_after_reload(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": temporary}):
            body = Path(temporary) / "body.txt"
            body.write_text("Holmes investigates the telegram. " * 90, encoding="utf-8")
            seal = Seal.objects.create(slug="flow-corrupt", name="Test")
            edition = Edition.objects.create(work=self.holmes, language=self.language, seal=seal)
            source = EditionBuild.objects.create(edition=edition, language_code="en", build_version=1,
                                                official_body_path=str(body), official_body_sha256=hashlib.sha256(body.read_bytes()).hexdigest())
            index_path = Path(temporary) / "corrupt.jsonl"
            index_path.write_text("{}\n", encoding="utf-8")
            SourceVectorIndex.objects.create(source=source, status="READY", source_hash=source.official_body_sha256,
                                            chunk_count=1, index_path=str(index_path), embedding_model="offline-test")
            ProjectReference.objects.create(project=self.project, intake_work=self.holmes, source=source)
            reloaded = StoryProject.objects.get(pk=self.project.pk)
            corpus = build_project_dashboard(reloaded, probe_models=False)["corpus"]
            self.assertFalse(corpus["all_vectorized"])
            self.assertEqual(corpus["normalized"], 1)
            self.assertEqual(corpus["vectorized"], 0)

    def test_continuity_cannot_be_silently_dropped_to_fit_context(self):
        with patch.dict(os.environ, {"GAIDEN_WRITER_MAX_CONTEXT_TOKENS": "100", "GAIDEN_WRITER_RESERVED_OUTPUT_TOKENS": "20"}):
            with self.assertRaises(ContextBudgetExceeded):
                allocate_context({"history": "Established facts " * 100})

    def test_reserved_output_does_not_reject_required_contract_input(self):
        sections = {
            "anchor_facts": "",
            "chapter_plan": "",
            "book_kit": "",
            "continuity_ledger": "",
            "history": "Required context. " * 5000,
            "reference_bible": "",
        }
        with patch.dict(os.environ, {"GAIDEN_WRITER_MAX_CONTEXT_TOKENS": "24000", "GAIDEN_WRITER_RESERVED_OUTPUT_TOKENS": "8000"}):
            budget = allocate_context(sections)
        self.assertLessEqual(budget["estimated_input"], 24000)
        self.assertEqual(budget["reserved_output"], 8000)

    def test_invalid_summary_does_not_save_partial_continuity(self):
        chapter = self.chapter(1, generated_text="Holmes examines the telegram.")
        generator = Mock()
        generator.generate_json.return_value = json.dumps({**self.summary(), "characters_present": "Holmes"})
        with patch("web.writer.services.chapter_closure.writer_generator", return_value=generator):
            with self.assertRaises(ValueError):
                close_chapter(chapter)
        self.assertFalse(self.project.chapter_summaries.exists())
        self.assertFalse(self.project.continuity_states.exists())

    def test_reference_bible_prioritizes_matching_author_and_respects_context_limit(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": temporary}):
            seal = Seal.objects.create(slug="flow-rag", name="Test")
            for work in (self.holmes, self.ackroyd):
                path = Path(temporary) / f"raw-{work.pk}.txt"
                path.write_text("Holmes examines the telegram. " * 90, encoding="utf-8")
                Edition.objects.create(work=work, language=self.language, seal=seal, raw_source_path=str(path))
                ProjectReference.objects.create(project=self.project, intake_work=work)
            prepare_project_corpus(self.project)
            embedder = Mock(model="offline-test")
            embedder.embed.side_effect = lambda texts: [[1.0, 0.5] for _ in texts]
            with patch("web.writer.services.references.embedding_client", return_value=embedder):
                vectorize_project_references(self.project)
            self.project.reference_bible = "Para Holmes e Watson: priorizar Arthur Conan Doyle.\n\nPara assassinatos: priorizar Agatha Christie."
            self.project.save()
            chapter = self.chapter(1, title="Holmes examines the telegram")
            hits = retrieve_source_rag(self.project, chapter, top_k=2)
            self.assertEqual(hits[0]["author"], "Arthur Conan Doyle")
            self.assertEqual({hit["author"] for hit in hits}, {"Arthur Conan Doyle", "Agatha Christie"})
            self.assertEqual(retrieve_source_rag(self.project, chapter, max_context_chars=100), [])

    def test_contract_uses_all_prior_summaries_without_prior_prose(self):
        for number in (1, 2):
            chapter = self.chapter(number, generated_text=f"PRIVATE PROSE {number}", status=Chapter.Status.FINAL)
            ChapterNarrativeSummary.objects.create(project=self.project, chapter=chapter, short_summary=f"Facts {number}", major_events=[f"Clue {number}"])
        with patch("web.writer.services.contracts._source_rag", return_value=[]):
            payload, _ = build_narrative_contract(self.chapter(3), persist=False)
            first_payload, _ = build_narrative_contract(self.project.chapters.get(number=1), persist=False)
        encoded = json.dumps(payload)
        self.assertNotIn("PRIVATE PROSE", encoded)
        self.assertIn("Facts 1", encoded)
        self.assertIn("Facts 2", encoded)
        self.assertEqual(first_payload["history"]["summaries"], [])

    def test_contract_drops_optional_rag_when_required_sections_fit(self):
        chapter = self.chapter(1)
        oversized_rag = [{"title": "Reference", "text": "Optional context. " * 5000}]
        with patch("web.writer.services.contracts._source_rag", return_value=oversized_rag), patch.dict(
            os.environ,
            {"GAIDEN_WRITER_MAX_CONTEXT_TOKENS": "500", "GAIDEN_WRITER_RESERVED_OUTPUT_TOKENS": "200"},
        ):
            payload, _ = build_narrative_contract(chapter, persist=False)
        self.assertEqual(payload["references"]["source_rag"], [])
        self.assertIn("source_rag", payload["token_budget"]["dropped_optional_sections"])

    def summary(self):
        return {"short_summary": "Holmes finds a telegram and decides to visit the bank.",
                "major_events": ["Telegram found"], "characters_present": ["Holmes", "Watson"],
                "locations": ["Baker Street"], "facts_established": ["The bank is threatened"],
                "new_information": ["The telegram bears a false signature"],
                "resolved_threads": [], "open_threads": ["Identify the sender"],
                "decisions": ["Visit the bank"], "state_changes": ["Investigation opened"]}

    def test_closure_generates_structured_facts_once_and_preserves_previous_ledger(self):
        chapter = self.chapter(1, generated_text="A telegram arrived. " * 50)
        generator = Mock()
        generator.generate_json.return_value = json.dumps(self.summary())
        with patch("web.writer.services.chapter_closure.writer_generator", return_value=generator):
            summary, ledger = close_chapter(chapter)
            repeated, repeated_ledger = close_chapter(chapter)
        generator.generate_json.assert_called_once()
        self.assertEqual(summary.pk, repeated.pk)
        self.assertEqual(ledger.pk, repeated_ledger.pk)
        self.assertEqual(summary.characters_present, ["Holmes", "Watson"])
        self.assertEqual(summary.major_events, ["Telegram found"])
        self.assertNotEqual(summary.short_summary, chapter.generated_text[:500])
        self.assertIn("The bank is threatened", ledger.state_json["plot"]["established_facts"])

    def test_failed_summary_does_not_finalize_or_unlock_next_chapter(self):
        chapter = self.chapter(1, generated_text="A telegram arrived.", status=Chapter.Status.OUTPUT_IMPORTED)
        following = self.chapter(2)
        with patch("web.writer.services.chapter_closure.writer_generator", side_effect=RuntimeError("unavailable")):
            self.client.post(reverse("writer:finalize", args=[chapter.pk]), {"confirm": "yes"})
        chapter.refresh_from_db()
        self.assertEqual(chapter.status, Chapter.Status.OUTPUT_IMPORTED)
        self.assertFalse(ChapterNarrativeSummary.objects.filter(chapter=chapter).exists())
        self.assertFalse(chapter_is_sequentially_eligible(following)[0])
