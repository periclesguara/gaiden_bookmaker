import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from web.writer.models import Chapter, ChapterSession, ManuscriptRevision, NarrativeContract, StoryProject
from web.writer.reference_bible import DEFAULT_REFERENCE_BIBLE, REFERENCE_BIBLE_PRECEDENCE
from web.writer.services.dashboard import build_project_dashboard
from web.writer.services.generation import build_chapter_request


class ReferenceBibleTests(TestCase):
    def setUp(self):
        self.project = StoryProject.objects.create(title="Existing project")
        self.chapter = Chapter.objects.create(
            project=self.project, number=1, title="Opening", script="A beginning",
            generated_text="Generated text", merged_text="Existing merge", final_text="Final text",
        )
        self.url = reverse("writer:project_reference_bible", args=[self.project.pk])

    def detail(self):
        with patch("web.writer.views.build_project_dashboard", side_effect=lambda project: build_project_dashboard(project, probe_models=False)):
            return self.client.get(reverse("writer:project_detail", args=[self.project.pk]))

    def test_old_project_opens_with_one_default_global_contract(self):
        self.assertEqual(self.project.reference_bible, "")
        response = self.detail()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="reference_bible"', count=1)
        self.assertContains(response, "Salvar Bíblia")
        self.assertContains(response, "REFERENCE BIBLE - GLOBAL CONTRACT")
        self.assertEqual(response.context["reference_bible_form"].initial["reference_bible"], DEFAULT_REFERENCE_BIBLE)

    def test_save_and_reload_preserve_long_text_and_line_breaks(self):
        text = "  Cronologia\n\n" + "Referência literária\n" * 10000 + "  "
        response = self.client.post(self.url, {"reference_bible": text})
        self.assertEqual(response.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.reference_bible, text)
        self.assertEqual(self.detail().context["reference_bible_form"].initial["reference_bible"], text)

    def test_save_only_updates_reference_bible_and_timestamp(self):
        ManuscriptRevision.objects.create(project=self.project, version=1, storage_path="existing/final.txt", sha256="a" * 64)
        ChapterSession.objects.create(chapter=self.chapter, number=1, content="Existing session")
        project_before = StoryProject.objects.values().get(pk=self.project.pk)
        chapters_before = list(Chapter.objects.values())
        sessions_before = list(ChapterSession.objects.values())
        revisions_before = list(ManuscriptRevision.objects.values())
        with patch("web.writer.services.projects.synchronize_chapters", side_effect=AssertionError("chapter synchronization")), patch("pathlib.Path.write_text", side_effect=AssertionError("editorial write")), patch("pathlib.Path.write_bytes", side_effect=AssertionError("editorial write")):
            self.assertEqual(self.client.post(self.url, {"reference_bible": "New canon"}).status_code, 302)
        project_after = StoryProject.objects.values().get(pk=self.project.pk)
        for field in project_before:
            if field not in {"reference_bible", "updated_at"}:
                self.assertEqual(project_before[field], project_after[field], field)
        self.assertEqual(chapters_before, list(Chapter.objects.values()))
        self.assertEqual(sessions_before, list(ChapterSession.objects.values()))
        self.assertEqual(revisions_before, list(ManuscriptRevision.objects.values()))
        self.assertEqual(NarrativeContract.objects.count(), 0)

    def test_save_preserves_canonical_and_merge_files_and_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = [Path(temporary) / "final.txt", Path(temporary) / "merge.txt"]
            revisions = []
            for version, path in enumerate(paths, 1):
                path.write_text(f"Existing editorial content {version}")
                revisions.append(ManuscriptRevision.objects.create(project=self.project, version=version, storage_path=str(path), sha256="a" * 64))
            self.project.canonical_manuscript = revisions[0]
            self.project.manuscript_merge_revision = revisions[1]
            self.project.save()
            before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]
            self.client.post(self.url, {"reference_bible": "Updated references"})
            self.project.refresh_from_db()
            self.assertEqual(self.project.canonical_manuscript_id, revisions[0].pk)
            self.assertEqual(self.project.manuscript_merge_revision_id, revisions[1].pk)
            self.assertEqual(before, [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths])

    def test_missing_field_does_not_clear_saved_value_but_explicit_empty_does(self):
        self.project.reference_bible = "Existing canon"
        self.project.save()
        self.client.post(self.url, {})
        self.project.refresh_from_db()
        self.assertEqual(self.project.reference_bible, "Existing canon")
        self.client.post(self.url, {"reference_bible": ""})
        self.project.refresh_from_db()
        self.assertEqual(self.project.reference_bible, "")

    def test_closed_project_is_read_only(self):
        self.project.status = StoryProject.Status.CLOSED
        self.project.reference_bible = "Closed canon"
        self.project.save()
        self.assertEqual(self.client.post(self.url, {"reference_bible": "Change"}).status_code, 403)
        self.assertNotContains(self.detail(), 'name="reference_bible"')
        self.project.refresh_from_db()
        self.assertEqual(self.project.reference_bible, "Closed canon")

    def test_generation_reads_full_global_context_without_database_writes(self):
        text = "Global canon\n" * 10000
        self.project.reference_bible = text
        self.project.save()
        before = list(Chapter.objects.values())
        request, _ = build_chapter_request(self.chapter)
        self.assertTrue(text in request.continuity, "Full reference bible missing from generation context")
        self.assertEqual(request.continuity.count(text), 1)
        self.assertEqual(before, list(Chapter.objects.values()))

    def test_empty_project_uses_default_global_contract_in_generation(self):
        request, _ = build_chapter_request(self.chapter)
        self.assertIn(DEFAULT_REFERENCE_BIBLE, request.continuity)
        self.assertIn("BIBLE\n   > CONTINUITY\n   > DIRECTION", request.continuity)

    def test_narrative_contract_uses_reference_bible_precedence(self):
        from web.writer.services.contracts import build_narrative_contract

        payload, _ = build_narrative_contract(self.chapter, persist=False)
        self.assertEqual(payload["references"]["reference_bible"], DEFAULT_REFERENCE_BIBLE)
        self.assertEqual(payload["priority_rules"], REFERENCE_BIBLE_PRECEDENCE)

    def test_save_endpoint_requires_post(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
