import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase, override_settings

from writer.models import Chapter, StoryProject
from writer.services import handoff as handoff_service
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
            rendered = body.decode()
            self.assertIn("## Chapter 01 — Opening", rendered)
            self.assertLess(
                rendered.index("## Chapter 01 — Opening"),
                rendered.index("## Chapter 02 — Closing"),
            )

    def test_export_is_idempotent_for_the_same_final_body(self):
        with TemporaryDirectory() as temporary, override_settings(WRITER_HANDOFF_ROOT=temporary):
            project = self._project()
            first = export_project_handoff(project)
            second = export_project_handoff(project)
            self.assertEqual(first, second)

    def test_rejects_divergent_body_without_overwriting_existing_package(self):
        with TemporaryDirectory() as temporary, override_settings(WRITER_HANDOFF_ROOT=temporary):
            project = self._project()
            destination = export_project_handoff(project)
            original_body = (destination / "body.md").read_bytes()
            original_manifest = (destination / "WRITER.HANDOFF.json").read_bytes()
            chapter = project.chapters.get(number=2)
            chapter.final_text = "A different final chapter."
            chapter.save(update_fields=("final_text",))

            with self.assertRaisesMessage(ValueError, "different body content"):
                export_project_handoff(project)

            self.assertEqual((destination / "body.md").read_bytes(), original_body)
            self.assertEqual(
                (destination / "WRITER.HANDOFF.json").read_bytes(), original_manifest
            )

    def test_rejects_manifest_with_inconsistent_body_metadata(self):
        with TemporaryDirectory() as temporary, override_settings(WRITER_HANDOFF_ROOT=temporary):
            project = self._project()
            destination = export_project_handoff(project)
            manifest_path = destination / "WRITER.HANDOFF.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["body"]["bytes"] += 1
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesMessage(ValueError, "manifest is inconsistent"):
                export_project_handoff(project)

    def test_write_failure_does_not_publish_a_partial_package(self):
        with TemporaryDirectory() as temporary, override_settings(WRITER_HANDOFF_ROOT=temporary):
            project = self._project()
            call_count = 0
            real_atomic_write = handoff_service._atomic_write

            def fail_manifest_write(path, data):
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise OSError("simulated manifest write failure")
                real_atomic_write(path, data)

            with patch.object(
                handoff_service, "_atomic_write", side_effect=fail_manifest_write
            ):
                with self.assertRaisesMessage(OSError, "simulated manifest write failure"):
                    export_project_handoff(project)

            destination = (
                Path(temporary)
                / f"project-{project.id:06d}"
                / "outbound"
            )
            self.assertFalse(destination.exists())
            self.assertEqual(
                list(destination.parent.glob(".handoff-package-*")), []
            )

    def test_rejects_project_with_unfinished_chapter(self):
        with TemporaryDirectory() as temporary, override_settings(WRITER_HANDOFF_ROOT=temporary):
            project = self._project()
            chapter = project.chapters.get(number=2)
            chapter.status = Chapter.Status.PLANNED
            chapter.save(update_fields=("status",))
            with self.assertRaisesMessage(ValueError, "pending: 02"):
                export_project_handoff(project)
