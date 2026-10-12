from __future__ import annotations

from unittest.mock import patch
import json

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from web.writer.models import Chapter, StoryProject, WriterExport
from web.writer.services.project_export import export_project_to_drive, import_return_files


class _Execute:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _Files:
    def __init__(self, returned=None):
        self.returned = returned or []
        self.queries = []

    def get(self, **_kwargs):
        return _Execute({
            "id": "project-root",
            "name": "Project",
            "mimeType": "application/vnd.google-apps.folder",
            "trashed": False,
            "webViewLink": "https://drive.test/project-root",
        })

    def list(self, **kwargs):
        self.queries.append(kwargs.get("q"))
        return _Execute({"files": self.returned})


class _Drive:
    def __init__(self, returned=None):
        self._files = _Files(returned)

    def files(self):
        return self._files


class ProjectDriveExportTests(TestCase):
    def setUp(self):
        self.project = StoryProject.objects.create(
            title="Sherlock Holmes: The Return of the Devil",
            language="en-GB",
            drive_project_folder_id="project-root",
            reference_bible="Use Doyle for Holmes and Watson.",
        )
        Chapter.objects.create(
            project=self.project,
            number=1,
            title="After Paris",
            direction="Return from Paris.",
            script="Watson narrates the unresolved case.",
            target_words=3500,
        )

    def test_project_export_persists_real_return_folder_and_manifest(self):
        folders = [
            {"id": "exports-id", "webViewLink": "https://drive.test/exports"},
            {"id": "export-id", "webViewLink": "https://drive.test/export"},
            {"id": "returns-id", "webViewLink": "https://drive.test/returns"},
            {"id": "chapter-return-id", "webViewLink": "https://drive.test/chapter-return"},
            {"id": "return-id", "webViewLink": "https://drive.test/return"},
        ]
        created = []

        def create_file(_drive, _parent, name, text, **_kwargs):
            created.append((name, text))
            return {"id": f"file-{len(created)}", "webViewLink": f"https://drive.test/file-{len(created)}"}

        with patch("web.writer.services.project_export._drive", return_value=_Drive()), patch(
            "web.writer.services.project_export._find_or_create_folder", side_effect=folders
        ), patch("web.writer.services.project_export._create_text_file", side_effect=create_file):
            export = export_project_to_drive(self.project, export_id="fixed-export")

        self.assertEqual(export.status, WriterExport.Status.READY)
        self.assertEqual(export.return_folder_id, "return-id")
        self.assertEqual(export.return_folder_url, "https://drive.test/return")
        self.assertEqual(export.manifest_json["return_folder_id"], "return-id")
        expected = export.manifest_json["expected_files"][0]
        self.assertEqual(expected["filename"], "chapter_01_response.md")
        self.assertEqual(expected["chapter_id"], str(self.project.chapters.get().pk))
        self.assertEqual(expected["export_version"], 1)
        self.assertEqual(export.manifest_json["contract_version"], "2.0")
        self.assertIn("CHAPTER NUMBER: 01", export.clipboard_contract)
        self.assertIn('"chapter_id": "' + str(self.project.chapters.get().pk) + '"', export.clipboard_contract)
        self.assertIn('"status": "COMPLETED"', export.clipboard_contract)
        self.assertIn("REPLACE_WITH_SHA256_OF_EXACT_UTF8_MD_BYTES", export.clipboard_contract)
        self.assertEqual({name for name, _ in created}, {"chapter_01_request.md", "export_contract.json"})

    def test_project_export_preserves_expected_files_when_drive_authentication_fails(self):
        with patch(
            "web.writer.services.project_export._drive",
            side_effect=RuntimeError("AUTH_REQUIRED: Google Drive authorization is required."),
        ):
            with self.assertRaisesRegex(ValueError, "AUTH_REQUIRED"):
                export_project_to_drive(self.project, export_id="failed-export")

        export = WriterExport.objects.get(export_id="failed-export")
        self.assertEqual(export.status, WriterExport.Status.FAILED)
        self.assertTrue(export.error_message.startswith("AUTH_REQUIRED"))
        self.assertEqual(len(export.expected_files), 1)
        self.assertEqual(export.expected_files[0]["filename"], "chapter_01_response.md")

    def test_chapters_can_be_exported_independently(self):
        second = Chapter.objects.create(project=self.project, number=2, title="The Message", target_words=3200)

        with patch(
            "web.writer.services.project_export._drive",
            side_effect=RuntimeError("AUTH_REQUIRED: Google Drive authorization is required."),
        ):
            with self.assertRaisesRegex(ValueError, "AUTH_REQUIRED"):
                export_project_to_drive(self.project, chapter=second, export_id="chapter-two-export")

        export = WriterExport.objects.get(export_id="chapter-two-export")
        self.assertEqual(len(export.expected_files), 1)
        self.assertEqual(export.expected_files[0]["chapter_number"], 2)
        self.assertEqual(export.expected_files[0]["chapter_id"], str(second.pk))

    def _ready_export(self, chapter, export_id="fixed-export", export_version=1):
        return WriterExport.objects.create(
            project=self.project,
            export_id=export_id,
            status=WriterExport.Status.READY,
            return_folder_id=f"return-{export_id}",
            return_folder_url=f"https://drive.test/return-{export_id}",
            expected_files=[{
                "project_id": str(self.project.pk),
                "chapter_id": str(chapter.pk),
                "chapter_number": chapter.number,
                "chapter_title": chapter.title,
                "filename": f"chapter_{chapter.number:02d}_response.md",
                "metadata_filename": f"chapter_{chapter.number:02d}_response.json",
                "export_id": export_id,
                "export_version": export_version,
                "contract_version": "2.0",
                "state": "EXPORTED",
                "target_words": chapter.target_words,
                "format": "md",
            }],
            manifest_json={"contract_version": "2.0"},
        )

    def _returned_files(self, chapter):
        return [
            {"id": "metadata-file", "name": f"chapter_{chapter.number:02d}_response.json", "mimeType": "application/json"},
            {"id": "returned-file", "name": f"chapter_{chapter.number:02d}_response.md", "mimeType": "text/markdown"},
        ]

    def _metadata(self, export, chapter, **overrides):
        value = {
            "project_id": str(self.project.pk), "chapter_id": str(chapter.pk),
            "export_id": export.export_id, "export_version": export.expected_files[0]["export_version"],
            "status": "completed", "content_file": f"chapter_{chapter.number:02d}_response.md",
        }
        value.update(overrides)
        return json.dumps(value)

    def test_return_accepts_uppercase_completed_and_validates_raw_sha256(self):
        chapter = self.project.chapters.get(number=1)
        export = self._ready_export(chapter)
        content = "Finished prose with final newline.\n"
        import hashlib
        metadata = self._metadata(
            export,
            chapter,
            status="COMPLETED",
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
        with patch("web.writer.services.project_export._drive", return_value=_Drive(self._returned_files(chapter))), patch(
            "web.writer.services.project_export._download_text",
            side_effect=lambda _drive, file_id: metadata if file_id == "metadata-file" else content,
        ):
            result = import_return_files(export)
        self.assertEqual(result["imported"], 1)
        chapter.refresh_from_db()
        self.assertEqual(chapter.generated_text, content.strip())

    def test_markdown_only_return_imports_from_the_linked_folder(self):
        chapter = self.project.chapters.get(number=1)
        export = self._ready_export(chapter)
        only_markdown = [self._returned_files(chapter)[1]]
        content = "# Chapter 01 - After Paris\n\nReturned prose.\n"
        with patch("web.writer.services.project_export._drive", return_value=_Drive(only_markdown)), patch(
            "web.writer.services.project_export._download_text", return_value=content
        ):
            self.assertEqual(import_return_files(export)["imported"], 1)
        chapter.refresh_from_db()
        export.refresh_from_db()
        self.assertEqual(chapter.generated_text, content.strip())
        self.assertEqual(chapter.generation_metadata["identity_source"], "linked_return_folder")
        self.assertEqual(export.manifest_json["imports"][0]["identity_source"], "linked_return_folder")
        self.assertEqual(export.expected_files[0]["state"], "IMPORTED")

    def test_import_button_downloads_markdown_without_json(self):
        chapter = self.project.chapters.get(number=1)
        export = self._ready_export(chapter)
        drive = _Drive([self._returned_files(chapter)[1]])
        with patch("web.writer.services.project_export._drive", return_value=drive), patch(
            "web.writer.services.project_export._download_text", return_value="Returned prose.\n"
        ):
            response = self.client.post(reverse("writer:project_export_import_return", args=[self.project.pk, export.export_id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("writer:project_detail", args=[self.project.pk]))
        self.assertIn(export.return_folder_id, drive.files().queries[0])
        chapter.refresh_from_db()
        self.assertEqual(chapter.generated_text, "Returned prose.")
        self.assertEqual(chapter.generation_metadata["identity_source"], "linked_return_folder")

    def test_missing_markdown_is_an_error_not_a_successful_empty_import(self):
        chapter = self.project.chapters.get(number=1)
        export = self._ready_export(chapter)
        with patch("web.writer.services.project_export._drive", return_value=_Drive()):
            with self.assertRaisesRegex(ValueError, "FILE_NOT_FOUND.*chapter_01_response.md"):
                import_return_files(export)
        chapter.refresh_from_db()
        self.assertEqual(chapter.generated_text, "")

    def test_markdown_only_return_rejects_conflicting_drive_identity(self):
        chapter = self.project.chapters.get(number=1)
        export = self._ready_export(chapter)
        item = {**self._returned_files(chapter)[1], "appProperties": {"chapter_id": "999"}}
        with patch("web.writer.services.project_export._drive", return_value=_Drive([item])):
            with self.assertRaisesRegex(ValueError, "CHAPTER_MISMATCH"):
                import_return_files(export)
        chapter.refresh_from_db()
        self.assertEqual(chapter.generated_text, "")

    def test_markdown_only_return_rejects_duplicate_filenames(self):
        chapter = self.project.chapters.get(number=1)
        export = self._ready_export(chapter)
        item = self._returned_files(chapter)[1]
        with patch("web.writer.services.project_export._drive", return_value=_Drive([item, {**item, "id": "other-file"}])):
            with self.assertRaisesRegex(ValueError, "IMPORT_FAILED.*duplicado"):
                import_return_files(export)
        chapter.refresh_from_db()
        self.assertEqual(chapter.generated_text, "")

    def test_present_but_invalid_json_does_not_fall_back_to_folder_identity(self):
        chapter = self.project.chapters.get(number=1)
        export = self._ready_export(chapter)
        with patch("web.writer.services.project_export._drive", return_value=_Drive(self._returned_files(chapter))), patch(
            "web.writer.services.project_export._download_text", return_value="not-json"
        ):
            with self.assertRaisesRegex(ValueError, "metadados de retorno inválidos"):
                import_return_files(export)
        chapter.refresh_from_db()
        self.assertEqual(chapter.generated_text, "")

    def test_return_rejects_declared_sha256_mismatch(self):
        chapter = self.project.chapters.get(number=1)
        export = self._ready_export(chapter)
        metadata = self._metadata(export, chapter, status="COMPLETED", sha256="0" * 64)
        with patch("web.writer.services.project_export._drive", return_value=_Drive(self._returned_files(chapter))), patch(
            "web.writer.services.project_export._download_text",
            side_effect=lambda _drive, file_id: metadata if file_id == "metadata-file" else "Changed prose.\n",
        ):
            with self.assertRaisesRegex(ValueError, "integridade inválida"):
                import_return_files(export)

    def test_return_import_validates_identity_preserves_history_and_skips_duplicate(self):
        chapter = self.project.chapters.get(number=1)
        export = self._ready_export(chapter)
        returned = self._returned_files(chapter)
        content = "# Chapter 1\n\nFinished prose."
        with patch("web.writer.services.project_export._drive", return_value=_Drive(returned)), patch(
            "web.writer.services.project_export._download_text",
            side_effect=lambda _drive, file_id: self._metadata(export, chapter) if file_id == "metadata-file" else content,
        ):
            first = import_return_files(export)
            second = import_return_files(export)

        self.assertEqual(first, {"found": 1, "imported": 1, "skipped": 0})
        self.assertEqual(second, {"found": 1, "imported": 0, "skipped": 1})
        chapter = self.project.chapters.get(number=1)
        self.assertEqual(chapter.status, Chapter.Status.OUTPUT_IMPORTED)
        self.assertEqual(chapter.generation_metadata["writer_export_id"], "fixed-export")
        export.refresh_from_db()
        self.assertEqual(export.expected_files[0]["state"], "IMPORTED")
        self.assertEqual(export.manifest_json["imports"][0]["content"], content)

    def test_return_for_another_chapter_is_rejected(self):
        chapter = self.project.chapters.get(number=1)
        other = Chapter.objects.create(project=self.project, number=2, title="The Message")
        export = self._ready_export(chapter)
        with patch("web.writer.services.project_export._drive", return_value=_Drive(self._returned_files(chapter))), patch(
            "web.writer.services.project_export._download_text",
            side_effect=lambda _drive, file_id: self._metadata(export, chapter, chapter_id=str(other.pk)) if file_id == "metadata-file" else "Wrong chapter.",
        ):
            with self.assertRaisesRegex(ValueError, "CHAPTER_MISMATCH"):
                import_return_files(export)
        chapter.refresh_from_db()
        self.assertEqual(chapter.generated_text, "")

    def test_chapter_two_can_be_imported_before_chapter_one(self):
        first = self.project.chapters.get(number=1)
        second = Chapter.objects.create(project=self.project, number=2, title="The Message")
        first_export = self._ready_export(first, export_id="chapter-one")
        second_export = self._ready_export(second, export_id="chapter-two")

        for export, chapter, content in (
            (second_export, second, "Chapter two returned first."),
            (first_export, first, "Chapter one returned second."),
        ):
            returned = self._returned_files(chapter)
            with patch("web.writer.services.project_export._drive", return_value=_Drive(returned)), patch(
                "web.writer.services.project_export._download_text",
                side_effect=lambda _drive, file_id, e=export, c=chapter, prose=content: self._metadata(e, c) if file_id == "metadata-file" else prose,
            ):
                self.assertEqual(import_return_files(export)["imported"], 1)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.generated_text, "Chapter one returned second.")
        self.assertEqual(second.generated_text, "Chapter two returned first.")

    def test_return_does_not_overwrite_approved_chapter(self):
        chapter = self.project.chapters.get(number=1)
        chapter.generated_text = "Approved prose."
        chapter.final_text = "Approved prose."
        chapter.status = Chapter.Status.FINAL
        chapter.save()
        export = self._ready_export(chapter)
        with patch("web.writer.services.project_export._drive", return_value=_Drive(self._returned_files(chapter))), patch(
            "web.writer.services.project_export._download_text",
            side_effect=lambda _drive, file_id: self._metadata(export, chapter) if file_id == "metadata-file" else "Replacement prose.",
        ):
            with self.assertRaisesRegex(ValueError, "não pode ser sobrescrito"):
                import_return_files(export)

    def test_project_page_shows_imported_return_details_and_next_step(self):
        chapter = self.project.chapters.get(number=1)
        chapter.status = Chapter.Status.OUTPUT_IMPORTED
        chapter.generated_text = "Imported chapter prose."
        chapter.generated_at = timezone.now()
        chapter.generation_metadata = {
            "return_file_id": "response-file-id",
            "return_filename": "chapter_01_response.md",
            "word_count": 3709,
            "sha256": "a" * 64,
            "writer_export_id": "fixed-export",
            "export_version": 2,
        }
        chapter.save()
        export = self._ready_export(chapter, export_version=2)
        export.expected_files = [{**export.expected_files[0], "state": "IMPORTED"}]
        export.contract_file_url = "https://drive.test/request"
        export.save(update_fields=("expected_files", "contract_file_url", "updated_at"))

        response = self.client.get(reverse("writer:project_detail", args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "IMPORTED")
        self.assertContains(response, "chapter_01_response.md")
        self.assertContains(response, "response-file-id")
        self.assertContains(response, "return-fixed-export")
        self.assertContains(response, "3709")
        self.assertContains(response, "Importação concluída")
        self.assertContains(response, "BLOQUEADA")
        self.assertContains(response, "Revisar e finalizar este capítulo")

    def test_project_page_keeps_transfer_report_isolated_per_chapter(self):
        first = self.project.chapters.get(number=1)
        second = Chapter.objects.create(
            project=self.project,
            number=2,
            title="The Message",
            direction="Watson receives a warning.",
            script="A sealed letter arrives at Baker Street.",
        )
        first_export = self._ready_export(first, export_id="chapter-one-export", export_version=2)
        first_export.return_folder_id = "return-chapter-one"
        first_export.expected_files = [{**first_export.expected_files[0], "state": "WAITING_RETURN"}]
        first_export.save(update_fields=("return_folder_id", "expected_files", "updated_at"))
        second_export = self._ready_export(second, export_id="chapter-two-export", export_version=1)
        second_export.return_folder_id = "return-chapter-two"
        second_export.expected_files = [{**second_export.expected_files[0], "state": "WAITING_RETURN"}]
        second_export.save(update_fields=("return_folder_id", "expected_files", "updated_at"))

        response = self.client.get(reverse("writer:project_detail", args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Exportação e retorno do capítulo 01")
        self.assertContains(response, "chapter-one-export")
        self.assertContains(response, "return-chapter-one")
        self.assertContains(response, "Exportação e retorno do capítulo 02")
        self.assertContains(response, "chapter-two-export")
        self.assertContains(response, "return-chapter-two")
        content = response.content.decode("utf-8")
        first_panel = content[content.index("Exportação e retorno do capítulo 01"):content.index("Exportação e retorno do capítulo 02")]
        second_panel = content[content.index("Exportação e retorno do capítulo 02"):]
        self.assertNotIn("chapter-two-export", first_panel)
        self.assertNotIn("return-chapter-two", first_panel)
        self.assertNotIn("chapter-one-export", second_panel)
        self.assertNotIn("return-chapter-one", second_panel)

    def test_project_page_explains_export_authentication_error(self):
        chapter = self.project.chapters.get(number=1)
        export = self._ready_export(chapter)
        export.status = WriterExport.Status.FAILED
        export.error_message = "TOKEN_EXPIRED: renew the existing Google CLI login."
        export.return_folder_id = ""
        export.return_folder_url = ""
        export.expected_files = [{**export.expected_files[0], "state": "EXPORT_ERROR"}]
        export.save(update_fields=("status", "error_message", "return_folder_id", "return_folder_url", "expected_files", "updated_at"))

        response = self.client.get(reverse("writer:project_detail", args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TOKEN_EXPIRED")
        self.assertContains(response, "Renovar a autorização do Google Drive")
        self.assertNotContains(response, f"exports/{export.export_id}/check-return/")
        self.assertNotContains(response, f"exports/{export.export_id}/import-return/")

    def test_project_and_chapter_pages_use_the_same_canonical_return_actions(self):
        chapter = self.project.chapters.get(number=1)
        export = self._ready_export(chapter)
        export.expected_files = [{**export.expected_files[0], "state": "WAITING_RETURN"}]
        export.save(update_fields=("expected_files", "updated_at"))

        for name, args in (("writer:project_detail", [self.project.pk]), ("writer:chapter_detail", [chapter.pk])):
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Exportação e retorno do capítulo 01")
                self.assertContains(response, "fixed-export")
                self.assertContains(response, "return-fixed-export")
                self.assertContains(response, reverse("writer:project_export_check_return", args=[self.project.pk, export.export_id]))
                self.assertContains(response, reverse("writer:project_export_import_return", args=[self.project.pk, export.export_id]))
                self.assertNotContains(response, reverse("writer:export_drive", args=[chapter.pk]))
                self.assertNotContains(response, reverse("writer:import_output", args=[chapter.pk]))

    def test_finalized_chapter_keeps_its_import_record_visible(self):
        chapter = self.project.chapters.get(number=1)
        chapter.status = Chapter.Status.FINAL
        chapter.final_text = "Approved prose."
        chapter.generated_at = timezone.now()
        chapter.generation_metadata = {
            "writer_export_id": "fixed-export",
            "return_file_id": "approved-response-id",
            "return_filename": "chapter_01_response.md",
            "word_count": 2,
            "sha256": "a" * 64,
        }
        chapter.save()
        self._ready_export(chapter)

        for name, args in (("writer:project_detail", [self.project.pk]), ("writer:chapter_detail", [chapter.pk])):
            with self.subTest(page=name):
                response = self.client.get(reverse(name, args=args))
                self.assertContains(response, "approved-response-id")
                self.assertContains(response, "Importação concluída")
                self.assertContains(response, "Salvar o resumo de continuidade")
                self.assertNotContains(response, reverse("writer:project_export_import_return", args=[self.project.pk, "fixed-export"]))
