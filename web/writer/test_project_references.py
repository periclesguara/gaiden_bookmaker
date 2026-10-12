import json
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import PropertyMock, patch

from django.test import TestCase
from django.urls import reverse

from editorial.models import Contributor, Edition, EditionBuild, Language, Seal, Work
from gaiden.writer_engine.corpus import chunk_source
from gaiden.writer_engine.index import VectorIndex
from pipeline.models import IntakeBatch, IntakeItem
from .forms import StoryProjectForm
from .models import ProjectReference, SourceDocument, SourceVectorIndex, StoryProject
from .services.corpus import _intake_item_path, prepare_project_corpus, safe_path
from .services.reference_catalog import serialize_work, work_queryset
from .services.dashboard import build_project_dashboard
from .services.references import vectorize_project_references, vectorize_source
from .services.source_retrieval import retrieve_source_rag


class ProjectReferenceSelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.language = Language.objects.create(code="ref-test", name="English", native_name="English")
        cls.doyle = Contributor.objects.create(name="Arthur Conan Doyle")
        cls.christie = Contributor.objects.create(name="Agatha Christie")
        cls.works = []
        for number, (title, author) in enumerate([
            ("Holmes", cls.doyle), ("Styles", cls.christie), ("Ackroyd", cls.christie),
        ]):
            cls.works.append(Work.objects.create(
                code="selector-test-" + str(number), title=title,
                author=author, original_language=cls.language,
            ))
        seal = Seal.objects.create(slug="selector-test", name="Test")
        edition = Edition.objects.create(work=cls.works[0], language=cls.language, seal=seal)
        cls.source = EditionBuild.objects.create(edition=edition, language_code="en", build_version=1)

    def payload(self, **changes):
        data = {
            "title": "Reference test", "language": "en", "chapter_count": "34",
            "project_languages": ["en", "pt-BR"], "rules_en": "English only",
            "rules_pt-BR": "Português brasileiro", "work_references": json.dumps([
                {"work_id": work.pk, "reference_role": "CANON" if index == 0 else "MYSTERY_STRUCTURE", "notes": "Use " + work.title}
                for index, work in enumerate(self.works)
            ]),
        }
        data.update(changes)
        return data

    def test_author_catalog_and_catalog_without_author_do_not_list_books(self):
        response = self.client.get(reverse("writer:reference_authors"), {"q": "Christie"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [{"id": self.christie.pk, "name": "Agatha Christie", "work_count": 2}])
        self.assertEqual(self.client.get(reverse("writer:work_catalog")).json()["results"], [])

    def test_author_catalog_returns_only_requested_author_and_includes_raw(self):
        response = self.client.get(reverse("writer:work_catalog"), {"author": self.christie.pk})
        rows = response.json()["results"]
        self.assertEqual({row["work_id"] for row in rows}, {self.works[1].pk, self.works[2].pk})
        self.assertTrue(all(row["state"] == "RAW" and not row["vectorized"] for row in rows))
        self.assertEqual(SourceVectorIndex.objects.count(), 0)

    def test_relative_intake_paths_resolve_inside_storage_root(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": temporary}):
            path = Path(temporary) / "02_IMPORTED_RAW" / "batch" / "book" / "source.txt"
            path.parent.mkdir(parents=True)
            path.write_text("reference body", encoding="utf-8")
            self.assertEqual(safe_path("02_IMPORTED_RAW/batch/book/source.txt"), path)

    def test_missing_registered_intake_file_is_downloaded_to_canonical_storage(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": temporary}):
            content = b"CHAPTER I\n\nReference body.\n"
            batch = IntakeBatch.objects.create(source_folder="01_INBOX_RAW/Test", status="REGISTERED")
            item = IntakeItem.objects.create(
                batch=batch, book_code="book-download", title="Download",
                remote_path="01_INBOX_RAW/Test/source.txt", relative_path="source.txt",
                original_name="source.txt", extension=".txt", size_bytes=len(content),
                source_language="en", target_language="", mime_type="text/plain",
                preview_operation="CREATE", status="REGISTERED",
                canonical_path="02_IMPORTED_RAW/batch/book/source.txt",
                sha256=hashlib.sha256(content).hexdigest(),
            )

            def download(canonical_path, destination):
                Path(destination).parent.mkdir(parents=True)
                Path(destination).write_bytes(content)

            with patch("web.writer.services.corpus.RcloneDriveStorage") as storage:
                storage.return_value.download_imported_to.side_effect = download
                self.assertEqual(_intake_item_path(item), Path(temporary) / item.canonical_path)
                storage.return_value.download_imported_to.assert_called_once()

    def test_preparation_reuses_existing_vectorized_source_document_by_title(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": temporary}):
            root = Path(temporary)
            body = root / "writer" / "normalized" / "study.txt"
            body.parent.mkdir(parents=True)
            body.write_text("CHAPTER I\n\n" + ("A reference body. " * 60), encoding="utf-8")
            digest = hashlib.sha256(body.read_bytes()).hexdigest()
            document = SourceDocument.objects.create(
                filename="ACD-SHERL - Sherlock Holmes: A Study in Scarlet.txt",
                source_path=str(body),
                normalized_path=str(body),
                normalized_sha256=digest,
                status=SourceDocument.Status.VECTORIZED,
            )
            work = Work.objects.create(
                code="study-source-document-test", title="A Study in Scarlet",
                author=self.doyle, original_language=self.language,
            )
            project = StoryProject.objects.create(title="Reuse existing document")
            ProjectReference.objects.create(project=project, intake_work=work)

            result = prepare_project_corpus(project)

        self.assertEqual(result, {"prepared": 0, "reused": 1, "pending": 0})
        index = SourceVectorIndex.objects.get(intake_work=work)
        self.assertEqual(index.normalized_document, document)
        self.assertEqual(index.status, SourceVectorIndex.Status.NOT_INDEXED)

    def test_preparation_reuses_sherlock_memoirs_for_later_reminiscences_alias(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": temporary}):
            root = Path(temporary)
            body = root / "writer" / "normalized" / "memoirs.txt"
            body.parent.mkdir(parents=True)
            body.write_text("CHAPTER I\n\n" + ("A reference body. " * 60), encoding="utf-8")
            digest = hashlib.sha256(body.read_bytes()).hexdigest()
            document = SourceDocument.objects.create(
                filename="ACD-SHER6 - Sherlock Holmes: the memoirs of Sherlock Holmes.txt",
                source_path=str(body),
                normalized_path=str(body),
                normalized_sha256=digest,
                status=SourceDocument.Status.VECTORIZED,
            )
            work = Work.objects.create(
                code="memoirs-alias-test", title="Sherlock Holmes:Some later reminiscences",
                author=self.doyle, original_language=self.language,
            )
            project = StoryProject.objects.create(title="Reuse alias document")
            ProjectReference.objects.create(project=project, intake_work=work)

            result = prepare_project_corpus(project)

        self.assertEqual(result, {"prepared": 0, "reused": 1, "pending": 0})
        self.assertEqual(SourceVectorIndex.objects.get(intake_work=work).normalized_document, document)

    def test_catalog_is_paginated_with_no_missing_works(self):
        Work.objects.bulk_create([
            Work(code="page-test-" + str(i), title="Page " + str(i), author=self.doyle, original_language=self.language)
            for i in range(25)
        ])
        first = self.client.get(reverse("writer:work_catalog"), {"author": self.doyle.pk}).json()
        second = self.client.get(reverse("writer:work_catalog"), {"author": self.doyle.pk, "page": first["next_page"]}).json()
        self.assertEqual(len(first["results"]), 20)
        self.assertEqual(len(first["results"]) + len(second["results"]), 26)

    def test_editorial_and_vector_states_are_independent(self):
        self.source.status = EditionBuild.STATUS_IN_PROGRESS
        self.source.save(update_fields=["status"])
        self.assertEqual(serialize_work(work_queryset().get(pk=self.works[0].pk))["state"], "IN PRODUCTION")
        self.source.status = EditionBuild.STATUS_DONE
        self.source.is_final = self.source.validation_passed = True
        self.source.save()
        row = serialize_work(work_queryset().get(pk=self.works[0].pk))
        self.assertEqual(row["state"], "EDITED")
        self.assertFalse(row["vectorized"])

    def test_save_multiple_authors_without_ai_and_create_chapters_idempotently(self):
        with patch("web.writer.services.references.VectorIndex.build") as build:
            response = self.client.post(reverse("writer:project_new"), self.payload())
            self.assertEqual(response.status_code, 302)
            project = StoryProject.objects.get(title="Reference test")
            self.assertEqual(project.references.count(), 3)
            self.assertEqual(project.chapters.count(), 34)
            self.client.post(reverse("writer:project_edit", args=[project.pk]), self.payload())
            self.assertEqual(project.chapters.count(), 34)
            self.assertEqual(project.references.count(), 3)
            self.assertFalse(project.chapters.exclude(generated_text="").exists())
            build.assert_not_called()
        self.assertEqual(project.language_rules.get(language_code="en").rules, "English only")
        self.assertEqual(project.language_rules.get(language_code="pt-BR").rules, "Português brasileiro")
        self.assertEqual(project.language_rules.filter(is_primary=True).count(), 1)
        self.assertEqual(project.references.get(intake_work=self.works[1]).roles, ["MYSTERY_STRUCTURE"])
        self.assertIsNone(project.references.get(intake_work=self.works[1]).source_id)

    def test_minimal_project_does_not_require_advanced_reference_configuration(self):
        response = self.client.post(reverse("writer:project_new"), {
            "title": "Minimal", "language": "en", "chapter_count": "1",
        })
        self.assertEqual(response.status_code, 302)
        project = StoryProject.objects.get(title="Minimal")
        self.assertEqual(project.language_rules.get(is_primary=True).language_code, "en")
        self.assertEqual(project.chapters.count(), 1)
        self.assertEqual(project.references.count(), 0)

    def test_project_form_adds_references_without_replacing_existing_ones(self):
        project = StoryProject.objects.create(title="Existing", language="en", reference_bible="Keep bible", drive_project_folder_id="keep-folder")
        ProjectReference.objects.create(project=project, intake_work=self.works[0], source=self.source, roles=["CANON"])
        response = self.client.post(reverse("writer:project_edit", args=[project.pk]), self.payload(work_references=json.dumps([
            {"work_id": self.works[1].pk, "reference_role": "MYSTERY_STRUCTURE", "notes": "Use Styles"},
            {"work_id": self.works[2].pk, "reference_role": "MYSTERY_STRUCTURE", "notes": "Use Ackroyd"},
        ])))
        self.assertEqual(response.status_code, 302)
        project.refresh_from_db()
        self.assertEqual(project.references.count(), 3)
        self.assertEqual(project.references.get(intake_work=self.works[0]).source_id, self.source.pk)
        self.assertEqual(project.reference_bible, "Keep bible")
        self.assertEqual(project.drive_project_folder_id, "keep-folder")

    def test_remove_reference_preserves_intake_and_advanced_settings(self):
        project = StoryProject.objects.create(title="Existing", language="en", reference_bible="Keep bible", drive_project_folder_id="keep-folder")
        reference = ProjectReference.objects.create(project=project, intake_work=self.works[1])
        response = self.client.post(reverse("writer:project_reference_remove", args=[project.pk, reference.pk]))
        self.assertEqual(response.status_code, 302)
        project.refresh_from_db()
        self.assertEqual(project.references.count(), 0)
        self.assertTrue(Work.objects.filter(pk=self.works[1].pk).exists())
        self.assertEqual(project.reference_bible, "Keep bible")
        self.assertEqual(project.drive_project_folder_id, "keep-folder")

    def test_legacy_get_has_no_writes_or_automatic_book_list(self):
        project = StoryProject.objects.create(title="Legacy", language="English (United Kingdom)")
        response = self.client.get(reverse("writer:project_edit", args=[project.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selecionar obras")
        self.assertNotContains(response, "Reference Corpus")
        self.assertNotContains(response, "Styles")
        self.assertEqual(project.language_rules.count(), 0)
        self.assertEqual(SourceVectorIndex.objects.count(), 0)

    def test_invalid_submission_preserves_selected_reference_and_notes(self):
        response = self.client.post(reverse("writer:project_new"), self.payload(title=""))
        self.assertEqual(response.status_code, 200)
        selected = response.context["selected_works"]
        self.assertEqual(len(selected), 3)
        self.assertEqual(next(row for row in selected if row["work_id"] == self.works[1].pk)["notes"], "Use Styles")

    def test_server_resolves_source_from_work_instead_of_trusting_foreign_source(self):
        form = StoryProjectForm(self.payload(work_references=json.dumps([
            {"work_id": self.works[1].pk, "source_id": self.source.pk, "reference_role": "OTHER"},
        ])))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["work_references"][0]["source_id"])

    def test_duplicate_work_is_deduplicated_and_bad_role_rejected(self):
        row = {"work_id": self.works[1].pk, "reference_role": "CANON"}
        form = StoryProjectForm(self.payload(work_references=json.dumps([row, row])))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len(form.cleaned_data["work_references"]), 1)
        form = StoryProjectForm(self.payload(work_references=json.dumps([{**row, "reference_role": ["CANON"]}])))
        self.assertFalse(form.is_valid())

    def test_reference_without_build_is_skipped_by_retrieval(self):
        project = StoryProject.objects.create(title="No build")
        ProjectReference.objects.create(project=project, intake_work=self.works[1])
        chapter = type("Brief", (), {"title": "case", "direction": "case", "script": "case"})()
        self.assertEqual(retrieve_source_rag(project, chapter), [])

    def test_vectorization_only_dispatches_the_selected_project_reference(self):
        project = StoryProject.objects.create(title="Selected project")
        reference = ProjectReference.objects.create(project=project, intake_work=self.works[0], source=self.source)
        ProjectReference.objects.create(project=project, intake_work=self.works[1])
        with patch("web.writer.views.vectorize_source") as vectorize:
            response = self.client.post(reverse("writer:reference_vectorize", args=[project.pk, reference.pk]))
            self.assertEqual(response.status_code, 302)
            vectorize.assert_called_once_with(self.source)
        other = StoryProject.objects.create(title="Other project")
        with patch("web.writer.views.vectorize_source") as vectorize:
            self.assertEqual(self.client.post(reverse("writer:reference_vectorize", args=[other.pk, reference.pk])).status_code, 404)
            vectorize.assert_not_called()

    def test_mismatched_reference_source_cannot_dispatch_vectorization(self):
        project = StoryProject.objects.create(title="Mismatched source")
        reference = ProjectReference.objects.create(project=project, intake_work=self.works[1], source=self.source)
        with patch("web.writer.views.vectorize_source") as vectorize:
            self.client.post(reverse("writer:reference_vectorize", args=[project.pk, reference.pk]))
            vectorize.assert_not_called()

    def test_existing_vectors_reused_and_stale_missing_index_not_reported_ready(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": temporary}):
            path = Path(temporary) / "index.jsonl"
            body = Path(temporary) / "body.txt"
            body.write_text("Holmes investigates the case. " * 80, encoding="utf-8")
            built = VectorIndex(model="offline-test", dimension=2, rows=[(chunk, [1.0, 0.5]) for chunk in chunk_source(body, body.parent)])
            built.save(path)
            self.source.official_body_path = str(body)
            self.source.official_body_sha256 = hashlib.sha256(body.read_bytes()).hexdigest()
            self.source.save(update_fields=["official_body_sha256", "official_body_path"])
            index = SourceVectorIndex.objects.create(source=self.source, source_hash=self.source.official_body_sha256,
                status=SourceVectorIndex.Status.READY, index_path=str(path), chunk_count=len(built.rows), embedding_model=built.model)
            with patch("web.writer.services.references.VectorIndex.build") as build:
                self.assertEqual(vectorize_source(self.source).pk, index.pk)
                build.assert_not_called()
            self.assertTrue(serialize_work(work_queryset().get(pk=self.works[0].pk))["vectorized"])
            path.unlink()
            self.assertFalse(serialize_work(work_queryset().get(pk=self.works[0].pk))["vectorized"])

    def test_project_reference_vectorization_reuses_ready_indexes_and_builds_only_missing(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": temporary}):
            first_body = Path(temporary) / "holmes.txt"
            second_body = Path(temporary) / "styles.txt"
            first_body.write_text("holmes investigation " * 80, encoding="utf-8")
            second_body.write_text("christie mystery " * 80, encoding="utf-8")
            first_hash = hashlib.sha256(first_body.read_bytes()).hexdigest()
            second_hash = hashlib.sha256(second_body.read_bytes()).hexdigest()
            self.source.status = EditionBuild.STATUS_DONE
            self.source.is_final = True
            self.source.validation_passed = True
            self.source.official_body_path = str(first_body)
            self.source.official_body_sha256 = first_hash
            self.source.save()
            second_edition = Edition.objects.create(work=self.works[1], language=self.language, seal=self.source.edition.seal)
            second_source = EditionBuild.objects.create(
                edition=second_edition, language_code="en", build_version=1,
                status=EditionBuild.STATUS_DONE, is_final=True, validation_passed=True,
                official_body_path=str(second_body), official_body_sha256=second_hash,
            )
            ready_path = Path(temporary) / "ready.jsonl"
            ready = VectorIndex(model="offline-test", dimension=2, rows=[(chunk, [1.0, 0.5]) for chunk in chunk_source(first_body, first_body.parent)])
            ready.save(ready_path)
            SourceVectorIndex.objects.create(
                source=self.source, source_hash=first_hash, status=SourceVectorIndex.Status.READY,
                index_path=str(ready_path), chunk_count=len(ready.rows), embedding_model=ready.model,
            )
            project = StoryProject.objects.create(title="Union")
            ProjectReference.objects.create(project=project, intake_work=self.works[0], source=self.source)
            ProjectReference.objects.create(project=project, intake_work=self.works[1], source=second_source)

            built = VectorIndex(model="offline-test", dimension=2, rows=[(chunk, [1.0, 0.5]) for chunk in chunk_source(second_body, second_body.parent)])

            with patch.object(EditionBuild, "qualifies_as_done", new_callable=PropertyMock, return_value=True), patch(
                "web.writer.services.references.VectorIndex.build", return_value=built
            ) as build, patch(
                "web.writer.services.references.embedding_client", return_value=object()
            ), patch("web.writer.services.references.writer_storage_root", return_value=Path(temporary)):
                result = vectorize_project_references(project)
                dashboard = build_project_dashboard(project, probe_models=False)

            self.assertEqual(result["selected"], 2)
            self.assertEqual(result["reused"], 1)
            self.assertEqual(result["vectorized"], 1)
            build.assert_called_once()
            self.assertEqual(dashboard["sources"]["selected"], 2)
            self.assertEqual(dashboard["sources"]["vectorized"], 2)
            self.assertTrue(dashboard["sources"]["all_vectorized"])
