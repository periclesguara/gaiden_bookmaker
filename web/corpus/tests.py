import copy
import tempfile
from pathlib import Path
from unittest.mock import patch
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.apps import apps
from django.test import TestCase

from editorial.models import Contributor, Edition, EditionPipeline, EditionText, Language, Seal, Work
SourceDocument = apps.get_model("writer", "SourceDocument")
StoryProject = apps.get_model("writer", "StoryProject")
from .models import CorpusChunk, CorpusSource, NormalizedArtifact, ProjectCorpus, VectorizedWork
from .services import (CorpusSourceResolver, batch_status, chunk_batch, normalize_batch,
                       preview_manifest, register_manifest, retrieve_for_project,
                       save_selection, vectorize_batch)
from .storage import digest, write_immutable
from .text import body_only, section_spans

CODES = ("book_0001", "book_0002", "book_0006", "book_0008", "book_0009", "book10", "book_011", "book_017", "book_018")


class FakeEmbedder:
    model = "offline-test-dense"
    model_digest = "a" * 64
    dimension = 3

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += len(texts)
        return [[1.0, float(int(digest(t.encode())[:4], 16)) + 1, 2.0] for t in texts]


class CorpusTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = patch.dict("os.environ", GAIDEN_STORAGE_ROOT=str(self.root))
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.author = Contributor.objects.create(name="Arthur Conan Doyle")
        self.language = Language.objects.create(code="en", name="English")
        self.seal = Seal.objects.create(slug="test-seal", name="Test imprint")
        self.works = [Work.objects.create(code=code, title=f"Novel {i}", author=self.author,
                       original_language=self.language) for i, code in enumerate(CODES)]
        self.manifest = {"batch_code": "doyle_sherlock_complete", "author_id": self.author.pk,
                         "author_name": self.author.name, "work_codes": list(CODES), "sources": []}
        for i, work in enumerate(self.works):
            text = ("*** START OF THE PROJECT GUTENBERG EBOOK TEST ***\nTechnical credits\n\n"
                    "Story One\n\n" + (f"The detective investigated evidence in story one of novel {i}. " * 130)
                    + "\n\nStory Two\n\n" + ("The second story resolved a different mystery. " * 130)
                    + "\n\n*** END OF THE PROJECT GUTENBERG EBOOK TEST ***\nLicense\n")
            path = self.root / f"raw-{i}.txt"
            path.write_text(text, encoding="utf-8")
            self.manifest["sources"].append({"work_code": work.code, "title": work.title,
                "provider": "GOOGLE_DRIVE", "provider_file_id": f"test-doc-{i}", "representation": "drive-text-lf-v1",
                "raw_path": str(path), "sha256": digest(path.read_bytes()),
                "provenance": {"body_start_heading": "Story One", "section_titles": ["Story One", "Story Two"]}})

    def prepared(self):
        batch = register_manifest(self.manifest)
        normalize_batch(batch)
        chunk_batch(batch)
        return batch

    def test_preview_creates_no_works_or_sources(self):
        preview = preview_manifest(self.manifest)
        self.assertEqual(preview["existing_work_matches"], 9)
        self.assertEqual(preview["new_works"], 0)
        self.assertEqual(preview["conflicts"], [])
        self.assertFalse(CorpusSource.objects.exists())

    def test_sherlock_manifest_cannot_reduce_approved_membership(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["sources"].pop()
        manifest["work_codes"].pop()
        self.assertTrue(preview_manifest(manifest)["conflicts"])
        with self.assertRaises(ValueError):
            register_manifest(manifest)

    def test_missing_batch_member_cannot_be_vectorized_as_complete(self):
        batch = self.prepared()
        batch.members.last().delete()
        with self.assertRaises(ValueError):
            vectorize_batch(batch, FakeEmbedder())
        self.assertFalse(VectorizedWork.objects.exists())
        self.assertEqual(batch_status(batch)["rag_status"], "BLOCKED")

    def test_conflicting_author_blocks_entire_batch(self):
        other = Contributor.objects.create(name="Unknown Author")
        Work.objects.filter(pk=self.works[3].pk).update(author=other)
        with self.assertRaises(ValueError):
            register_manifest(self.manifest)
        self.assertFalse(CorpusSource.objects.exists())

    def test_missing_source_blocks_all_registration(self):
        Path(self.manifest["sources"][-1]["raw_path"]).unlink()
        with self.assertRaises(ValueError):
            register_manifest(self.manifest)
        self.assertEqual(CorpusSource.objects.count(), 0)

    def test_idempotent_register_preserves_work_ids(self):
        ids = list(Work.objects.values_list("pk", flat=True))
        register_manifest(self.manifest)
        register_manifest(self.manifest)
        self.assertEqual(CorpusSource.objects.count(), 9)
        self.assertEqual(list(Work.objects.values_list("pk", flat=True)), ids)

    def test_changed_canonical_source_requires_review(self):
        register_manifest(self.manifest)
        altered = copy.deepcopy(self.manifest)
        path = self.root / "different-source.txt"
        path.write_text("A different source" * 100)
        altered["sources"][0].update(raw_path=str(path), sha256=digest(path.read_bytes()))
        with self.assertRaises(ValueError):
            register_manifest(altered)
        self.assertEqual(CorpusSource.objects.count(), 9)

    def test_inline_edition_raw_is_valid_without_physical_path(self):
        edition = Edition.objects.create(work=self.works[0], language=self.language, seal=self.seal)
        EditionText.objects.create(edition=edition, raw_path="/missing/raw.txt", raw_text="Original literary source" * 50)
        resolved = CorpusSourceResolver().resolve(self.works[0].pk)
        self.assertIn("raw_text", resolved)
        self.assertEqual(resolved["origin"], "BOOKMAKER_LEGACY")

    def test_public_canonical_source_precedes_legacy_edition(self):
        edition = Edition.objects.create(work=self.works[0], language=self.language, seal=self.seal)
        EditionText.objects.create(edition=edition, raw_text="Older body" * 100)
        register_manifest(self.manifest)
        resolved = CorpusSourceResolver().resolve(self.works[0].pk)
        self.assertEqual(resolved["origin"], "GOOGLE_DRIVE")
        self.assertTrue(resolved["canonical_for_rag"])

    def test_normalize_preserves_raw_and_records_provenance(self):
        batch = register_manifest(self.manifest)
        normalize_batch(batch)
        self.assertEqual(NormalizedArtifact.objects.count(), 9)
        for entry in self.manifest["sources"]:
            self.assertEqual(digest(Path(entry["raw_path"]).read_bytes()), entry["sha256"])
        for artifact in NormalizedArtifact.objects.all():
            self.assertTrue(artifact.source_sha256 and artifact.normalized_sha256 and artifact.normalizer_version)
            text = Path(artifact.normalized_path).read_text()
            self.assertIn("Story Two", text)
            self.assertNotIn("GUTENBERG", text)

    def test_missing_reviewed_story_rolls_back_all_normalization(self):
        batch = register_manifest(self.manifest)
        source = CorpusSource.objects.order_by("pk").last()
        source.provenance["section_titles"].append("Absent Story")
        source.save(update_fields=("provenance",))
        with self.assertRaises(ValueError):
            normalize_batch(batch)
        self.assertEqual(NormalizedArtifact.objects.count(), 0)
        batch.refresh_from_db()
        self.assertEqual(batch.status, "FAILED")

    def test_chunk_requires_every_work_normalized(self):
        batch = register_manifest(self.manifest)
        with self.assertRaises(ValueError):
            chunk_batch(batch)
        self.assertFalse(CorpusChunk.objects.exists())

    def test_chunks_preserve_story_boundaries_and_metadata(self):
        self.prepared()
        for work in self.works:
            chunks = CorpusChunk.objects.filter(work=work)
            self.assertEqual(set(chunks.values_list("story_or_section", flat=True)), {"Story One", "Story Two"})
            for c in chunks:
                self.assertEqual(c.author_id, work.author_id)
                self.assertEqual(c.source.work_id, work.pk)
                self.assertEqual(c.artifact.work_id, work.pk)
                self.assertEqual(c.text_sha256, digest(c.text.encode()))
                if c.story_or_section == "Story One":
                    self.assertNotIn("Story Two", c.text)

    def test_dense_vector_counts_match_chunks_and_fingerprint(self):
        batch = self.prepared()
        vectorize_batch(batch, FakeEmbedder())
        status = batch_status(batch)
        self.assertEqual(status["vectorized"], 9)
        self.assertEqual(status["rag_status"], "READY")
        self.assertEqual(status["embedding_count"], CorpusChunk.objects.count())
        self.assertEqual(len(status["batch_fingerprint"]), 64)

    def test_incomplete_embeddings_do_not_leave_partial_ready_rows(self):
        batch = self.prepared()
        embedder = FakeEmbedder()
        embedder.embed = lambda texts: [[1, 2, 3]]
        with self.assertRaises(ValueError):
            vectorize_batch(batch, embedder)
        self.assertEqual(VectorizedWork.objects.count(), 0)
        self.assertEqual(batch_status(batch)["rag_status"], "BLOCKED")

    def test_reuse_performs_no_new_embedding_work(self):
        batch = self.prepared()
        embedder = FakeEmbedder()
        vectorize_batch(batch, embedder)
        calls = embedder.calls
        normalized = list(NormalizedArtifact.objects.values_list("pk", flat=True))
        chunks = CorpusChunk.objects.count()
        normalize_batch(batch)
        chunk_batch(batch)
        vectorize_batch(batch, embedder)
        self.assertEqual(embedder.calls, calls)
        self.assertEqual(list(NormalizedArtifact.objects.values_list("pk", flat=True)), normalized)
        self.assertEqual(CorpusChunk.objects.count(), chunks)

    def test_new_project_empty_selection_and_filtered_retrieval_after_reopen(self):
        batch = self.prepared()
        embedder = FakeEmbedder()
        vectorize_batch(batch, embedder)
        project = StoryProject.objects.create(title="Untitled project")
        self.assertEqual(project.canonical_corpus.backend, "CANONICAL_CORPUS")
        self.assertFalse(project.canonical_corpus.works.exists())
        selected = {self.works[0].pk, self.works[2].pk}
        save_selection(project, selected)
        reopened = StoryProject.objects.get(pk=project.pk)
        self.assertEqual(set(reopened.canonical_corpus.works.values_list("pk", flat=True)), selected)
        hits = retrieve_for_project(reopened, "detective mystery", embedder)
        self.assertTrue(hits)
        self.assertTrue({h["work_id"] for h in hits}.issubset(selected))

    def test_legacy_project_and_done_edition_remain_identical(self):
        project = StoryProject.objects.create(title="The Devil in Paris", vector_index_path="untouched-index.jsonl")
        ProjectCorpus.objects.filter(project=project).update(backend="LEGACY_WRITER")
        doc = SourceDocument.objects.create(filename="legacy.txt", source_path=str(self.root / "legacy.txt"))
        project.sources.add(doc)
        edition = Edition.objects.create(work=self.works[0], language=self.language, seal=self.seal)
        pipeline = EditionPipeline.objects.create(edition=edition, current_stage="DONE")
        original = (project.vector_index_path, project.updated_at, set(project.sources.values_list("pk", flat=True)))
        batch = self.prepared()
        vectorize_batch(batch, FakeEmbedder())
        project.refresh_from_db()
        pipeline.refresh_from_db()
        self.assertEqual((project.vector_index_path, project.updated_at, set(project.sources.values_list("pk", flat=True))), original)
        self.assertEqual(pipeline.current_stage, "DONE")
        with self.assertRaises(ValueError):
            save_selection(project, [self.works[0].pk])

    def test_changed_normalized_file_blocks_rag_for_whole_batch(self):
        batch = self.prepared()
        vectorize_batch(batch, FakeEmbedder())
        artifact = NormalizedArtifact.objects.first()
        Path(artifact.normalized_path).write_text("corrupted normalized body")
        self.assertEqual(batch_status(batch)["rag_status"], "BLOCKED")

    def test_changed_chunk_provenance_blocks_entire_batch(self):
        batch = self.prepared()
        vectorize_batch(batch, FakeEmbedder())
        chunk = CorpusChunk.objects.first()
        other = CorpusSource.objects.exclude(pk=chunk.source_id).first()
        CorpusChunk.objects.filter(pk=chunk.pk).update(source=other)
        self.assertEqual(batch_status(batch)["rag_status"], "BLOCKED")

    def test_canonical_source_cannot_escape_storage(self):
        entry = self.manifest["sources"][0]
        entry["raw_path"] = "/etc/hosts"
        self.assertTrue(preview_manifest(self.manifest)["conflicts"])
        with self.assertRaises(ValueError):
            write_immutable("../../outside.txt", b"unsafe")

    def test_multiline_gutenberg_marker_and_narrative_preface_preserved(self):
        raw = "*** START OF THE PROJECT GUTENBERG EBOOK THE CASE-BOOK OF SHERLOCK\nHOLMES ***\nCredits\nPREFACE\n" + "A literary preface. " * 100 + "\nStory One\n" + "Narrative text. " * 100 + "\n*** END OF THE PROJECT GUTENBERG EBOOK BOOK ***\nLicense"
        text, report = body_only(raw, {"body_start_heading": "PREFACE", "section_titles": ["PREFACE", "Story One"]})
        self.assertTrue(text.startswith("PREFACE"))
        self.assertIn("A literary preface.", text)
        self.assertNotIn("HOLMES ***", text)
        self.assertEqual(report["section_count"], 2)

    def test_duplicate_adjacent_story_headings_do_not_split_twice(self):
        spans = section_spans("Story One\n\nStory One\nLiterary text\nStory Two\nOther text", ["Story One", "Story Two"])
        self.assertEqual([s[0] for s in spans], ["Story One", "Story Two"])

    def test_author_scope_does_not_clear_other_selected_works(self):
        project = StoryProject.objects.create(title="Selection project")
        save_selection(project, [self.works[0].pk])
        save_selection(project, [self.works[1].pk], scope=[self.works[1].pk, self.works[2].pk])
        self.assertEqual(set(project.canonical_corpus.works.values_list("pk", flat=True)),
                         {self.works[0].pk, self.works[1].pk})

    def test_identical_source_preserves_both_provider_origins(self):
        register_manifest(self.manifest)
        other = copy.deepcopy(self.manifest)
        for entry in other["sources"]:
            entry["provider_file_id"] += "-second-origin"
        register_manifest(other)
        self.assertEqual(CorpusSource.objects.count(), 9)
        self.assertEqual(len(CorpusSource.objects.first().provenance["origins"]), 2)

    def test_bm25_cannot_mark_dense_batch_ready(self):
        batch = self.prepared()
        embedder = FakeEmbedder()
        embedder.model = "local-lexical-bm25"
        with self.assertRaises(ValueError):
            vectorize_batch(batch, embedder)
        self.assertEqual(VectorizedWork.objects.count(), 0)

    def test_catalog_requires_staff_and_shows_author_work_count(self):
        self.assertEqual(self.client.get("/corpus/").status_code, 302)
        user = get_user_model().objects.create_user(username="corpus-operator", is_staff=True)
        self.client.force_login(user)
        response = self.client.get("/corpus/", {"author": self.author.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Arthur Conan Doyle")
        self.assertContains(response, "9 obras")

    def test_selection_http_persists_only_checked_works(self):
        user = get_user_model().objects.create_user(username="selector", is_staff=True)
        self.client.force_login(user)
        project = StoryProject.objects.create(title="New project")
        ids = [self.works[0].pk, self.works[2].pk]
        response = self.client.post(f"/corpus/projects/{project.pk}/", {"works": ids, "scope": [w.pk for w in self.works]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(project.canonical_corpus.works.values_list("pk", flat=True)), set(ids))

    def test_older_gutenberg_footer_before_star_marker_is_removed(self):
        raw = "Chapter I\n" + "Literary ending remains intact. " * 100 + "\nEnd of Project Gutenberg's Novel, by Author\n*** END OF THIS PROJECT GUTENBERG EBOOK NOVEL ***\nLicense"
        text, _ = body_only(raw, {"body_start_heading": "Chapter I"})
        self.assertNotIn("Gutenberg", text)
        self.assertTrue(text.rstrip().endswith("Literary ending remains intact."))

    @skipUnless(any(field.name == "status" for field in StoryProject._meta.fields), "Closed projects belong to the operator Writer schema")
    def test_closed_project_selection_is_read_only(self):
        project = StoryProject.objects.create(title="Closed project")
        save_selection(project, [self.works[0].pk])
        StoryProject.objects.filter(pk=project.pk).update(status="CLOSED")
        with self.assertRaises(ValueError):
            save_selection(project, [self.works[1].pk])
        self.assertEqual(list(project.canonical_corpus.works.values_list("pk", flat=True)), [self.works[0].pk])

    def test_writer_adapter_empty_selection_never_calls_model(self):
        from .writer_adapter import source_rag
        project = StoryProject.objects.create(title="Empty project")
        chapter = apps.get_model("writer", "Chapter")(title="Mystery")
        with patch("corpus.writer_adapter.configured_embedder") as configure:
            self.assertEqual(source_rag(project, chapter), [])
        configure.assert_not_called()

    def test_generic_writer_engine_adds_dense_query_prefix(self):
        from .embedding import QueryEmbedder
        embedder = FakeEmbedder()
        with patch.object(embedder, "embed", return_value=[[1.0, 2.0, 3.0]]) as call:
            self.assertEqual(QueryEmbedder(embedder).embed(["detective"]), [[1.0, 2.0, 3.0]])
        call.assert_called_once_with(["search_query: detective"])

    def test_writer_adapter_uses_dense_selected_work_only(self):
        from .writer_adapter import source_rag, scope_status
        batch = self.prepared()
        embedder = FakeEmbedder()
        vectorize_batch(batch, embedder)
        project = StoryProject.objects.create(title="Dense project")
        save_selection(project, [self.works[0].pk, self.works[2].pk])
        chapter = apps.get_model("writer", "Chapter")(title="Detective mystery")
        with patch("corpus.writer_adapter.configured_embedder", return_value=embedder):
            hits = source_rag(project, chapter)
        self.assertTrue(hits)
        self.assertTrue({h["work_id"] for h in hits}.issubset({self.works[0].pk, self.works[2].pk}))
        self.assertEqual(scope_status(project)["selected"], 2)
        self.assertTrue(scope_status(project)["all_vectorized"])
