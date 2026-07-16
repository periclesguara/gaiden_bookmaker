import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from gaiden.application.intake.drive_sync import synchronize_drive_folder
from gaiden.application.intake.ingestion import ingest_bytes, ingest_path
from gaiden.application.intake.translation import (
    confirm_ready_for_editing,
    prepare_for_codex,
    register_translation_return,
)
from gaiden.domain.intake import IntakeState
from gaiden.infrastructure import intake_storage
from gaiden.infrastructure.intake_drive import DriveFile, RcloneClient
from web.intake_module.models import IntakeBatch, IntakeItem


class FakeConverter:
    def convert_to_markdown(self, source_path):
        return Path(source_path).read_text(encoding="utf-8")


class FakeDriveClient:
    def __init__(self, files, payloads):
        self.files = files
        self.payloads = payloads
        self.checked = False
        self.download_statuses = []

    def check_available(self):
        self.checked = True

    def list_files(self, relative_path):
        return self.files

    def download_file(self, folder, drive_file, destination):
        self.download_statuses.append(
            IntakeItem.objects.get(drive_file_id=drive_file.file_id).status
        )
        destination.write_bytes(self.payloads[drive_file.name])
        return destination


class IntakeWorkflowTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="gaiden-intake-workflow-")
        self.addCleanup(temporary.cleanup)
        self.temporary_root = Path(temporary.name)
        self.storage_root = self.temporary_root / "data"
        environment = patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.storage_root)}, clear=False)
        environment.start()
        self.addCleanup(environment.stop)
        self.batch = IntakeBatch.objects.create(
            code="batch_0001",
            name="Independent Books",
            source_language="en",
            drive_relative_path="Edgar_Rice_borroughs",
        )

    def ingest(self, name="book.txt", payload=b"Copyright 1900\n\nChapter One\nThe story.\n"):
        return ingest_bytes(self.batch, name, payload, converter=FakeConverter())

    def test_original_remains_unchanged_and_clean_text_is_created(self):
        payload = b"Copyright 1900\n\nChapter One\nThe story.\n"
        result = self.ingest(payload=payload)
        item = result["item"]
        original = intake_storage.resolve_stored_path(item.original_path)
        cleaned = intake_storage.resolve_stored_path(item.clean_path)
        self.assertEqual(original.read_bytes(), payload)
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), item.source_sha256)
        self.assertNotIn("Copyright 1900", cleaned.read_text(encoding="utf-8"))
        self.assertIn("Chapter One\nThe story.", cleaned.read_text(encoding="utf-8"))
        self.assertEqual(item.status, IntakeState.CLEAN_READY.value)

    def test_symlink_input_is_rejected(self):
        source = self.temporary_root / "source.txt"
        source.write_text("text", encoding="utf-8")
        link = self.temporary_root / "link.txt"
        link.symlink_to(source)
        with self.assertRaises(intake_storage.IntakeStorageError):
            ingest_path(self.batch, link, converter=FakeConverter())

    def test_absolute_and_windows_style_filenames_are_rejected(self):
        for filename in ("/tmp/book.txt", "C:\\books\\book.txt", "../book.txt"):
            with self.subTest(filename=filename):
                with self.assertRaises(intake_storage.IntakeStorageError):
                    ingest_bytes(self.batch, filename, b"text", converter=FakeConverter())

    def test_atomic_write_cleans_partial_file_on_replace_failure(self):
        target = intake_storage.clean_path(self.batch.code, "en", 1)
        with patch("gaiden.infrastructure.intake_storage.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaises(OSError):
                intake_storage.atomic_write_text(target, "payload")
        self.assertFalse(target.exists())
        self.assertEqual(list(target.parent.glob(f".{target.name}.*")), [])

    def test_duplicate_is_detected_by_sha256(self):
        first = self.ingest("one.txt", b"same payload")
        second = self.ingest("renamed.txt", b"same payload")
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertFalse(first["item"].is_duplicate)
        self.assertTrue(second["item"].is_duplicate)
        self.assertEqual(second["item"].duplicate_of_id, first["item"].id)

    def test_same_size_with_different_hashes_is_not_duplicate(self):
        first = self.ingest("one.txt", b"abcd")
        second = self.ingest("two.txt", b"wxyz")
        self.assertEqual(first["item"].source_size, second["item"].source_size)
        self.assertNotEqual(first["item"].source_sha256, second["item"].source_sha256)
        self.assertFalse(second["duplicate"])

    def test_png_is_ignored_without_blocking_drive_batch(self):
        files = [
            DriveFile("img", "cover.png", "cover.png", 3),
            DriveFile("txt", "story.txt", "story.txt", 10),
        ]
        client = FakeDriveClient(files, {"story.txt": b"Story body"})
        report = synchronize_drive_folder(
            self.batch,
            "Edgar_Rice_borroughs",
            client=client,
            converter=FakeConverter(),
        )
        self.assertTrue(client.checked)
        self.assertEqual(len(report["ignored"]), 1)
        self.assertEqual(report["ignored"][0]["filename"], "cover.png")
        self.assertEqual(len(report["imported"]), 1)
        self.assertEqual(self.batch.items.count(), 1)
        self.assertEqual(client.download_statuses, [IntakeState.DOWNLOADING.value])

    def test_translation_manifest_and_language_isolation(self):
        de_item = self.ingest("german.txt", b"German target source")["item"]
        fr_item = self.ingest("french.txt", b"French target source")["item"]
        de_manifest = prepare_for_codex(de_item, target_language="de")
        fr_manifest = prepare_for_codex(fr_item, target_language="fr")
        self.assertEqual(de_manifest["batch_code"], self.batch.code)
        self.assertEqual(de_manifest["item_id"], de_item.id)
        self.assertEqual(de_manifest["source_language"], "en")
        self.assertEqual(de_manifest["target_language"], "de")
        self.assertIn("/de/", f"/{de_item.translation_input_path}/")
        self.assertIn("/fr/", f"/{fr_item.translation_input_path}/")
        self.assertNotEqual(de_item.translation_input_path, fr_item.translation_input_path)

    def test_wrong_item_and_language_returns_are_rejected(self):
        item = self.ingest()["item"]
        prepare_for_codex(item, target_language="de")
        with self.assertRaises(ValueError):
            register_translation_return(item, "item_9999_clean_translate_de.txt", b"translated")
        with self.assertRaises(ValueError):
            register_translation_return(item, "item_0001_clean_translate_fr.txt", b"translated")
        item.refresh_from_db()
        self.assertEqual(item.status, IntakeState.READY_FOR_CODEX.value)

    def test_empty_and_non_utf8_returns_are_rejected(self):
        item = self.ingest()["item"]
        manifest = prepare_for_codex(item, target_language="de")
        expected_name = Path(manifest["expected_return_path"]).name
        with self.assertRaisesRegex(ValueError, "empty"):
            register_translation_return(item, expected_name, b"  \n")
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            register_translation_return(item, expected_name, b"\xff\xfe")

    def test_valid_return_can_be_confirmed_for_editing(self):
        item = self.ingest()["item"]
        manifest = prepare_for_codex(item, target_language="de")
        expected_name = Path(manifest["expected_return_path"]).name
        digest = register_translation_return(item, expected_name, "Übersetzt".encode("utf-8"))
        self.assertEqual(digest, hashlib.sha256("Übersetzt".encode("utf-8")).hexdigest())
        confirm_ready_for_editing(item)
        item.refresh_from_db()
        self.assertEqual(item.status, IntakeState.READY_FOR_EDITING.value)

    def test_rclone_client_uses_argument_list_without_shell(self):
        completed = subprocess.CompletedProcess(["rclone", "version"], 0, stdout="rclone v1", stderr="")
        with patch("gaiden.infrastructure.intake_drive.shutil.which", return_value="/usr/bin/rclone"):
            with patch("gaiden.infrastructure.intake_drive.subprocess.run", return_value=completed) as run:
                RcloneClient(timeout=7).check_available()
        arguments, kwargs = run.call_args
        self.assertEqual(arguments[0], ["rclone", "version"])
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["timeout"], 7)
        self.assertEqual(kwargs["env"]["HOME"], os.environ["HOME"])

    def test_rclone_lists_direct_inbox_folders_with_lsd(self):
        completed = subprocess.CompletedProcess(
            ["rclone", "lsd", "gaiden_drive:01_INBOX_RAW"],
            0,
            stdout="          -1 2026-07-16 10:00:00        -1 Edgar_Rice_borroughs\n",
            stderr="",
        )
        with patch("gaiden.infrastructure.intake_drive.subprocess.run", return_value=completed) as run:
            folders = RcloneClient(timeout=9).list_folders("")
        self.assertEqual(folders, ["Edgar_Rice_borroughs"])
        arguments, kwargs = run.call_args
        self.assertEqual(arguments[0], ["rclone", "lsd", "gaiden_drive:01_INBOX_RAW"])
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["timeout"], 9)

    @patch("gaiden.infrastructure.converters.markitdown_adapter.MarkItDownAdapter.convert_to_markdown")
    @patch("gaiden.infrastructure.intake_drive.subprocess.run")
    def test_multiple_browser_upload_uses_ingestion_without_external_call(self, subprocess_run, convert):
        convert.side_effect = lambda path: Path(path).read_text(encoding="utf-8")
        response = self.client.post(
            reverse("intake_module:batch_upload", args=[self.batch.id]),
            data={
                "files": [
                    SimpleUploadedFile("one.txt", b"Book one", content_type="text/plain"),
                    SimpleUploadedFile("two.txt", b"Book two", content_type="text/plain"),
                ]
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.batch.items.count(), 2)
        subprocess_run.assert_not_called()

    def test_all_action_pages_and_routes_are_available(self):
        item = self.ingest()["item"]
        self.assertEqual(self.client.get(reverse("intake_module:batch_drive", args=[self.batch.id])).status_code, 200)
        self.assertEqual(self.client.get(reverse("intake_module:item_detail", args=[item.id])).status_code, 200)
