import hashlib
import json
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings

from writer.models import Chapter, StoryProject
from writer.services.handoff import export_project_handoff


class WriterHandoffTests(TestCase):
    def _project(self):
        project = StoryProject.objects.create(title="The Test Book", chapter_count=2)
        Chapter.objects.create(
            project=project, number=1, title="Opening",
            status=Chapter.Status.FINAL, final_text="First chapter body.",
        )
        Chapter.objects.create(
            project=project, number=2, title="Closing",
            status=Chapter.Status.FINAL, final_text="Second chapter body.",
        )
        return project

    def test_exports_body_and_manifest_without_external_model_call(self):
        with TemporaryDirectory() as temporary, override_settings(WRITER_HANDOFF_ROOT=temporary):
            destination = export_project_handoff(self._project())
            body = (destination / "body.md").read_bytes()
            manifest = json.loads((destination / "WRITER.HANDOFF.json").read_text())
            self.assertEqual(manifest["status"], "AWAITING_GPT_PLUS_WORK")
            self.assertEqual(manifest["next_step"]["return_status"], "GAIDEN_BODY_READY")
            self.assertEqual(manifest["next_step"]["skip_stages"], ["BLOCK_01"])
            self.assertEqual(manifest["body"]["sha256"], hashlib.sha256(body).hexdigest())
            self.assertIn("## Chapter 01 — Opening", body.decode())

    def test_export_is_idempotent_for_the_same_final_body(self):
        with TemporaryDirectory() as temporary, override_settings(WRITER_HANDOFF_ROOT=temporary):
            project = self._project()
            first = export_project_handoff(project)
            second = export_project_handoff(project)
            self.assertEqual(first, second)

    def test_rejects_project_with_unfinished_chapter(self):
        with TemporaryDirectory() as temporary, override_settings(WRITER_HANDOFF_ROOT=temporary):
            project = self._project()
            chapter = project.chapters.get(number=2)
            chapter.status = Chapter.Status.PLANNED
            chapter.save(update_fields=("status",))
            with self.assertRaisesMessage(ValueError, "pending: 02"):
                export_project_handoff(project)
