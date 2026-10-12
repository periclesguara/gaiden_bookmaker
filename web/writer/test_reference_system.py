import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

from django.db import IntegrityError, transaction
from django.test import TestCase

from editorial.models import Contributor, Edition, EditionBuild, Language, Seal, Work
from gaiden.writer_engine.corpus import SourceChunk, chunk_source
from gaiden.writer_engine.index import VectorIndex
from web.writer.models import ProjectReference, SourceVectorIndex, StoryProject
from web.writer.reference_bible import DEFAULT_REFERENCE_BIBLE, effective_reference_bible
from web.writer.services.references import index_status, vectorize_source
from web.writer.services.source_retrieval import retrieve_source_rag


class ReferenceSystemTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        language = Language.objects.create(code="ref-en", name="English", native_name="English")
        author = Contributor.objects.create(name="Reference Author")
        seal = Seal.objects.create(slug="reference-tests", name="Reference Tests")
        self.sources = []
        for number in range(1, 4):
            work = Work.objects.create(code=f"reference-{number}", title=f"Source {number}", original_language=language, author=author)
            edition = Edition.objects.create(work=work, language=language, seal=seal)
            body = self.root / f"source-{number}.txt"
            body.write_text((f"grain policy source {number} " * 80), encoding="utf-8")
            digest = hashlib.sha256(body.read_bytes()).hexdigest()
            self.sources.append(EditionBuild.objects.create(
                edition=edition, language_code="en", build_version=1, status=EditionBuild.STATUS_DONE,
                is_final=True, validation_passed=True, official_body_path=str(body), official_body_sha256=digest,
            ))

    def test_reference_bible_defaults_empty_and_global_contract_is_effective(self):
        project = StoryProject.objects.create(title="Project")
        self.assertEqual(project.reference_bible, "")
        self.assertEqual(effective_reference_bible(project), DEFAULT_REFERENCE_BIBLE)
        project.reference_bible = "References support facts only."
        project.save(update_fields=("reference_bible",))
        self.assertEqual(StoryProject.objects.get(pk=project.pk).reference_bible, "References support facts only.")
        self.assertEqual(effective_reference_bible(project), "References support facts only.")

    def test_duplicate_reference_is_prevented_and_delete_preserves_source(self):
        project = StoryProject.objects.create(title="Project")
        ProjectReference.objects.create(project=project, source=self.sources[0])
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProjectReference.objects.create(project=project, source=self.sources[0])
        project.delete()
        self.assertTrue(EditionBuild.objects.filter(pk=self.sources[0].pk).exists())

    def test_changed_source_hash_marks_ready_index_outdated(self):
        source = self.sources[0]
        SourceVectorIndex.objects.create(source=source, source_hash="old", status=SourceVectorIndex.Status.READY)
        self.assertEqual(index_status(source).status, SourceVectorIndex.Status.OUTDATED)

    def test_ready_index_is_shared_by_multiple_projects(self):
        first = StoryProject.objects.create(title="First")
        second = StoryProject.objects.create(title="Second")
        ProjectReference.objects.create(project=first, source=self.sources[0])
        ProjectReference.objects.create(project=second, source=self.sources[0])
        index = SourceVectorIndex.objects.create(
            source=self.sources[0], source_hash=self.sources[0].official_body_sha256,
            status=SourceVectorIndex.Status.READY, chunk_count=4,
        )
        self.assertEqual(index_status(self.sources[0]).pk, index.pk)
        self.assertEqual(SourceVectorIndex.objects.filter(source=self.sources[0]).count(), 1)

    def test_revectorization_replaces_metadata_on_same_index(self):
        source = self.sources[0]
        existing = SourceVectorIndex.objects.create(source=source, status=SourceVectorIndex.Status.OUTDATED)
        destination = self.root / "index.jsonl"

        class Built:
            model = "local-test"
            rows = [(object(), [1.0])]
            def save(self, path):
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text('{}\n', encoding="utf-8")

        with patch.object(EditionBuild, "qualifies_as_done", new_callable=PropertyMock, return_value=True), patch(
            "web.writer.services.references.VectorIndex.build", return_value=Built()
        ), patch("web.writer.services.references.embedding_client", return_value=object()), patch(
            "web.writer.services.references.writer_storage_root", return_value=self.root
        ), patch.dict(
            os.environ, {"GAIDEN_STORAGE_ROOT": str(self.root), "GAIDEN_DATA_ROOT": str(self.root)}
        ):
            result = vectorize_source(source)
        self.assertEqual(result.pk, existing.pk)
        self.assertEqual(result.status, SourceVectorIndex.Status.READY)
        self.assertEqual(result.source_hash, source.official_body_sha256)
        self.assertEqual(result.embedding_model, "local-test")

    def test_vector_failure_is_recorded_and_retryable(self):
        source = self.sources[0]
        with patch.object(EditionBuild, "qualifies_as_done", new_callable=PropertyMock, return_value=True), patch(
            "web.writer.services.references.VectorIndex.build", side_effect=RuntimeError("embedding failed")
        ), patch(
            "web.writer.services.references.embedding_client", return_value=object()
        ), patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.root), "GAIDEN_WRITER_STORAGE_ROOT": str(self.root)}):
            with self.assertRaisesMessage(RuntimeError, "embedding failed"):
                vectorize_source(source)
        index = SourceVectorIndex.objects.get(source=source)
        self.assertEqual(index.status, SourceVectorIndex.Status.FAILED)
        self.assertIn("embedding failed", index.error_message)

    def test_retrieval_never_crosses_project_reference_boundary(self):
        project_a = StoryProject.objects.create(title="A")
        project_b = StoryProject.objects.create(title="B")
        for project in (project_a, project_b):
            config = project.canonical_corpus
            config.backend = "LEGACY_WRITER"
            config.save(update_fields=("backend",))
        for source in self.sources[:2]:
            ProjectReference.objects.create(project=project_a, source=source, roles=["PRIMARY_SOURCE"])
        ProjectReference.objects.create(project=project_b, source=self.sources[2], roles=["PRIMARY_SOURCE"])
        for source in self.sources:
            body = Path(source.official_body_path)
            index = VectorIndex(model="offline-test", dimension=2, rows=[(chunk, [1.0, 0.5]) for chunk in chunk_source(body, body.parent)])
            path = self.root / f"index-{source.pk}.jsonl"
            index.save(path)
            SourceVectorIndex.objects.create(source=source, source_hash=source.official_body_sha256, status=SourceVectorIndex.Status.READY,
                                            index_path=str(path), embedding_model=index.model, chunk_count=len(index.rows))
        chapter = SimpleNamespace(title="Grain", direction="grain policy", script="investigate grain policy")
        with patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.root)}):
            hits = retrieve_source_rag(project_a, chapter, roles=["PRIMARY_SOURCE"], top_k=8)
        self.assertTrue(hits)
        self.assertTrue({hit["canonical_work_id"] for hit in hits}.issubset({self.sources[0].edition.work_id, self.sources[1].edition.work_id}))
        self.assertNotIn(self.sources[2].edition.work_id, {hit["canonical_work_id"] for hit in hits})
        self.assertTrue(all({"title", "author", "chunk_id", "source_version"}.issubset(hit) for hit in hits))

    def test_project_without_references_skips_retrieval(self):
        project = StoryProject.objects.create(title="No references")
        chapter = SimpleNamespace(title="Chapter", direction="grain policy", script="grain")
        self.assertEqual(retrieve_source_rag(project, chapter), [])
