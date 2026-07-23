import hashlib
import json
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, transaction
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from editorial.models import EditionPipeline
from gaiden.application.intake.book_codes import assign_book_code
from gaiden.application.intake.pipeline_handoff import open_in_bookmaker
from gaiden.application.intake.reconciliation import update_duplicate_group
from gaiden.application.pipeline import drive_return, official_body
from gaiden.domain.intake import IntakeState
from gaiden.domain.languages import canonical_language, internal_language
from gaiden.infrastructure import intake_storage, storage
from gaiden.infrastructure.intake_drive import DriveFile
from pipeline.models import OfficialBodyPromotion, OfficialBodySnapshot
from pipeline.services.block_status import resolve_block_two_completion
from web.intake_module.models import IntakeBatch, IntakeItem, TranslationJob


class ExportClient:
    def __init__(self):
        self.uploads = []
        self.remote_sizes = {}
        self.checked = 0

    def check_available(self):
        self.checked += 1

    def upload_file_to_path(self, source, folder, filename):
        key = (folder, filename)
        size = Path(source).stat().st_size
        no_op = self.remote_sizes.get(key) == size
        self.remote_sizes[key] = size
        self.uploads.append((Path(source), folder, filename))
        return {"remote_path": f"{folder}/{filename}", "no_op": no_op}


class ReturnClient:
    def __init__(self, job, payload, *, completed_stages=None, overrides=None):
        manifest = drive_return._job_manifest(job)
        if completed_stages is not None:
            manifest["completed_stages"] = completed_stages
        manifest.update(overrides or {})
        manifest_payload = (json.dumps(manifest) + "\n").encode("utf-8")
        self.payloads = {
            job.expected_return_filename: payload,
            job.manifest_filename: manifest_payload,
        }
        self.files = [
            DriveFile("txt", job.expected_return_filename, job.expected_return_filename, len(payload)),
            DriveFile("manifest", job.manifest_filename, job.manifest_filename, len(manifest_payload)),
        ]
        self.checked = 0
        self.listed = []
        self.downloaded = []

    def check_available(self):
        self.checked += 1

    def list_files(self, folder):
        self.listed.append(folder)
        return list(self.files)

    def download_file(self, folder, drive_file, destination):
        self.downloaded.append((folder, drive_file.name))
        destination.write_bytes(self.payloads[drive_file.name])
        return destination


class TranslationJobV2Tests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="gaiden-translation-v2-")
        self.addCleanup(temporary.cleanup)
        self.storage_root = Path(temporary.name) / "data"
        environment = patch.dict(
            os.environ,
            {
                "GAIDEN_STORAGE_ROOT": str(self.storage_root),
                "GAIDEN_RETURN_MIN_BYTES": "1",
                "GAIDEN_RETURN_WARN_MIN_RATIO": "0.1",
                "GAIDEN_RETURN_WARN_MAX_RATIO": "10",
            },
            clear=False,
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.batch = IntakeBatch.objects.create(
            code="job-v2",
            name="Job V2",
            author_default="Edgar Rice Burroughs",
            source_language="en",
            public_domain=True,
        )
        original = intake_storage.original_path("job-v2", "en", 1, ".txt")
        intake_storage.atomic_write_text(original, "CHAPTER 1\n\nTarzan begins.\n")
        payload = original.read_bytes()
        self.item = IntakeItem.objects.create(
            batch=self.batch,
            order_index=1,
            source_filename="tarzan.txt",
            source_format="txt",
            source_size=len(payload),
            source_sha256=hashlib.sha256(payload).hexdigest(),
            confirmed_title="Tarzan of the Apes",
            original_year=1912,
            target_language="en-us",
            book_code="book_0042",
            original_path=intake_storage.relative_storage_path(original),
            status=IntakeState.CLEAN_READY.value,
        )
        self.edition = open_in_bookmaker(self.item).edition
        self.clean = storage.heading_cleaner_dir(self.item.book_code) / "clean.txt"
        self.clean.parent.mkdir(parents=True, exist_ok=True)
        self.clean.write_text(
            "CHAPTER 1\n\nTarzan begins in the jungle. {{IMAGE:CH01:01}}\n",
            encoding="utf-8",
        )

    def export(self, output_stage="translated", client=None):
        client = client or ExportClient()
        result = drive_return.export_translation_job(
            self.edition,
            client=client,
            output_stage=output_stage,
        )
        return TranslationJob.objects.get(job_id=result.job_id), result, client

    def good_payload(self):
        return b"CHAPTER 1\n\nTarzan continues through the jungle. {{IMAGE:CH01:01}}\n"

    def test_language_normalization_has_one_external_format(self):
        self.assertEqual(canonical_language("en_us"), "en-us")
        self.assertEqual(canonical_language("ptbr"), "pt-br")
        self.assertEqual(internal_language("en-us"), "en_us")
        with self.assertRaises(ValueError):
            canonical_language("../../en")

    def test_heading_cleaner_missing_and_empty_block_export(self):
        self.clean.unlink()
        with self.assertRaisesRegex(drive_return.DriveReturnError, "not available"):
            self.export()
        self.clean.write_text("  \n", encoding="utf-8")
        with self.assertRaisesRegex(drive_return.DriveReturnError, "empty"):
            self.export()

    def test_export_creates_frozen_manifest_v2_and_canonical_paths(self):
        job, result, client = self.export()
        manifest = json.loads((storage.data_dir() / job.manifest_path).read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "gaiden_translation_job_v2")
        self.assertEqual(manifest["job_id"], str(job.job_id))
        self.assertEqual(manifest["edition_id"], self.edition.id)
        self.assertEqual(manifest["intake_item_id"], self.item.id)
        self.assertEqual(job.input_folder, "book_0042/en-us/input")
        self.assertEqual(job.expected_return_folder, "book_0042/en-us/return")
        self.assertTrue(job.expected_return_filename.endswith("_translated_en-us.txt"))
        self.assertEqual(len(client.uploads), 2)
        self.assertEqual(result.manifest_filename, job.manifest_filename)
        self.assertFalse(Path(job.manifest_path).is_absolute())

    def test_reexport_same_sha_is_idempotent(self):
        client = ExportClient()
        first, _result, _client = self.export(client=client)
        second, result, _client = self.export(client=client)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(TranslationJob.objects.count(), 1)
        self.assertTrue(result.no_op)

    def test_changed_input_creates_new_job_and_supersedes_old(self):
        first, _result, client = self.export()
        self.clean.write_text(
            "CHAPTER 1\n\nChanged frozen source. {{IMAGE:CH01:01}}\n",
            encoding="utf-8",
        )
        second, _result, _client = self.export(client=client)
        first.refresh_from_db()
        self.assertNotEqual(first.job_id, second.job_id)
        self.assertEqual(first.status, TranslationJob.STATUS_SUPERSEDED)
        self.assertEqual(second.status, TranslationJob.STATUS_EXPORTED)

    def test_title_change_does_not_change_frozen_return_name(self):
        job, _result, _client = self.export()
        expected = job.expected_return_filename
        self.item.confirmed_title = "A Changed Mutable Title"
        self.item.save(update_fields=["confirmed_title", "updated_at"])
        link = drive_return.resolve_drive_return_link(self.edition, output_stage="translated")
        self.assertEqual(link.canonical_filename, expected)

    def test_official_and_translated_names_are_unambiguous(self):
        translated, _result, _client = self.export("translated")
        official, _result, _client = self.export("official")
        self.assertIn("_translated_en-us.txt", translated.expected_return_filename)
        self.assertIn("_official_en-us.txt", official.expected_return_filename)

    def test_wrong_filename_and_two_candidates_fail_closed(self):
        job, _result, _client = self.export()
        client = ReturnClient(job, self.good_payload())
        client.files[0] = DriveFile("wrong", "wrong.txt", "wrong.txt", 4)
        with self.assertRaisesRegex(drive_return.DriveReturnError, "Exactly one return TXT"):
            drive_return.import_drive_return(self.edition, client=client, output_stage="translated")
        client = ReturnClient(job, self.good_payload())
        client.files.append(client.files[0])
        with self.assertRaisesRegex(drive_return.DriveReturnError, "Exactly one return TXT"):
            drive_return.import_drive_return(self.edition, client=client, output_stage="translated")

    def test_manifest_identity_fields_are_enforced(self):
        fields = {
            "job_id": "00000000-0000-0000-0000-000000000000",
            "edition_id": 999999,
            "intake_item_id": 999999,
            "book_code": "book_9999",
            "target_language": "fr",
            "output_stage": "official",
        }
        for field, value in fields.items():
            with self.subTest(field=field):
                job, _result, _client = self.export()
                client = ReturnClient(job, self.good_payload(), overrides={field: value})
                with self.assertRaisesRegex(drive_return.DriveReturnError, "identity mismatch"):
                    drive_return.import_drive_return(self.edition, client=client, output_stage="translated")

    def test_official_manifest_must_confirm_external_pipeline(self):
        job, _result, _client = self.export("official")
        client = ReturnClient(job, self.good_payload(), completed_stages=["translation"])
        with self.assertRaisesRegex(drive_return.DriveReturnError, "translation, refine, and polish"):
            drive_return.import_drive_return(self.edition, client=client, output_stage="official")

    def test_invalid_utf8_empty_and_identical_returns_fail(self):
        cases = [(b"\xff\xfe", "invalid_utf8"), (b"", "empty_content"), (self.clean.read_bytes(), "identical_to_input")]
        for payload, reason in cases:
            with self.subTest(reason=reason):
                job, _result, _client = self.export()
                client = ReturnClient(job, payload)
                with self.assertRaisesRegex(drive_return.DriveReturnError, reason):
                    drive_return.import_drive_return(self.edition, client=client, output_stage="translated")

    def test_protected_marker_loss_and_language_mismatch_fail(self):
        job, _result, _client = self.export()
        missing_marker = ReturnClient(job, b"CHAPTER 1\n\nTarzan continues safely.\n")
        with self.assertRaisesRegex(drive_return.DriveReturnError, "protected_markers_missing"):
            drive_return.import_drive_return(self.edition, client=missing_marker, output_stage="translated")

        Spanish = ("CHAPTER 1\n\n" + "de la el y que los " * 20 + "{{IMAGE:CH01:01}}\n").encode()
        wrong_language = ReturnClient(job, Spanish)
        with self.assertRaisesRegex(drive_return.DriveReturnError, "detected_language_mismatch"):
            drive_return.import_drive_return(self.edition, client=wrong_language, output_stage="translated")

    def test_heading_change_is_warning_and_not_auto_processed(self):
        job, _result, _client = self.export()
        client = ReturnClient(job, b"Tarzan continues. {{IMAGE:CH01:01}}\n")
        result = drive_return.import_drive_return(
            self.edition, client=client, output_stage="translated"
        )
        self.assertEqual(result.validation_status, drive_return.WARNING)
        with self.assertRaisesRegex(drive_return.DriveReturnError, "audited editorial confirmation"):
            drive_return.validated_pending_payload(self.edition, output_stage="translated")
        job.refresh_from_db()
        self.assertEqual(job.validation_status, "WARNING_REQUIRES_CONFIRMATION")
        report = json.loads((storage.data_dir() / job.validation_report_path).read_text(encoding="utf-8"))
        self.assertIn("heading_count_changed", report["warnings"])

        user = get_user_model().objects.create_user(username="editor", password="safe-test-password")
        self.client.force_login(user)
        response = self.client.post(
            reverse(
                "pipeline_drive_return_confirm_warning",
                kwargs={"edition_id": self.edition.id},
            ),
            {
                "output_stage": "translated",
                "confirmation_note": "Estrutura revisada; o heading foi intencionalmente removido.",
            },
        )
        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.warning_confirmed_by, "editor")
        self.assertIsNotNone(job.warning_confirmed_at)
        payload, pending, confirmed_job = drive_return.validated_pending_payload(
            self.edition, output_stage="translated"
        )
        self.assertEqual(payload, pending.pending_path.read_bytes())
        self.assertEqual(confirmed_job.pk, job.pk)
        report = json.loads((storage.data_dir() / job.validation_report_path).read_text(encoding="utf-8"))
        self.assertEqual(report["editorial_confirmation"]["actor"], "editor")

    def test_warning_confirmation_requires_authentication_and_justification(self):
        job, _result, _client = self.export()
        drive_return.import_drive_return(
            self.edition,
            client=ReturnClient(job, b"Tarzan continues. {{IMAGE:CH01:01}}\n"),
            output_stage="translated",
        )
        url = reverse(
            "pipeline_drive_return_confirm_warning",
            kwargs={"edition_id": self.edition.id},
        )
        self.client.post(url, {"output_stage": "translated", "confirmation_note": "reviewed"})
        job.refresh_from_db()
        self.assertIsNone(job.warning_confirmed_at)
        user = get_user_model().objects.create_user(username="editor-2", password="safe-test-password")
        self.client.force_login(user)
        self.client.post(url, {"output_stage": "translated", "confirmation_note": ""})
        job.refresh_from_db()
        self.assertIsNone(job.warning_confirmed_at)

    def test_translated_return_continues_to_refine_without_official_promotion(self):
        job, _result, _client = self.export("translated")
        client = ReturnClient(job, self.good_payload())
        path = drive_return.import_translated_return(self.edition, client=client)
        self.assertTrue(path.is_file())
        self.assertIsNone(official_body.resolve_official_body(self.edition))
        pipeline = EditionPipeline.objects.get(edition=self.edition)
        self.assertEqual(pipeline.current_stage, "TRANSLATED")
        self.assertEqual(pipeline.translation_language, "en_us")
        with self.assertRaises(drive_return.DriveReturnError):
            drive_return.save_pending_as_official(self.edition)

    def test_official_return_promotes_and_preserves_previous_version(self):
        previous = official_body.promote(
            self.edition,
            b"CHAPTER 1\n\nPrevious official.\n",
            provenance="manual_editorial_approval",
            source_stage="manual",
        )
        job, _result, _client = self.export("official")
        client = ReturnClient(
            job,
            self.good_payload(),
            completed_stages=["translation", "refine", "polish"],
        )
        result = drive_return.import_and_promote_drive_return(self.edition, client=client)
        self.assertNotEqual(previous.snapshot_id, result.snapshot_id)
        self.assertEqual(OfficialBodySnapshot.objects.filter(edition=self.edition, is_active=True).count(), 1)
        old = OfficialBodySnapshot.objects.get(pk=previous.snapshot_id)
        self.assertFalse(old.is_active)
        self.assertTrue(official_body.resolve_storage_path(old.relative_path).is_file())
        job.refresh_from_db()
        self.assertEqual(job.status, TranslationJob.STATUS_COMPLETED)

    def test_repeated_same_sha_promotion_is_no_op(self):
        payload = b"CHAPTER 1\n\nStable official text.\n"
        first = official_body.promote(
            self.edition,
            payload,
            provenance="internal_polish",
            source_stage="polish",
        )
        second = official_body.promote(
            self.edition,
            payload,
            provenance="internal_polish",
            source_stage="polish",
        )
        self.assertTrue(second.no_op)
        self.assertEqual(first.snapshot_id, second.snapshot_id)

    def test_internal_polish_completes_block_two_without_drive(self):
        source = storage.builds_dir(self.item.book_code, "en-us") / "merge_polish.txt"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("CHAPTER 1\n\nInternally polished.\n", encoding="utf-8")
        official_body.promote_internal_polish(self.edition, source)
        pipeline = EditionPipeline.objects.get(edition=self.edition)
        completion = resolve_block_two_completion(self.edition, pipeline)
        self.assertTrue(completion.done)
        self.assertIn("internal_polish", completion.reason)

    def test_kdp_build_source_requires_the_official_body(self):
        from editorial.kdp_mode import resolve_miolo_source_path
        from pipeline.services import md_quality

        fallback = storage.builds_dir(self.item.book_code, "en-us") / "BOOK.MD_FINAL"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_text("Fallback must not be selected.", encoding="utf-8")
        with self.assertRaises(FileNotFoundError):
            resolve_miolo_source_path(self.edition)
        official_body.promote(
            self.edition,
            b"CHAPTER 1\n\nOfficial source.\n",
            provenance="internal_polish",
            source_stage="polish",
        )
        self.assertEqual(resolve_miolo_source_path(self.edition), official_body.canonical_path(self.edition))

        pre_edition = storage.builds_dir(self.item.book_code, "en") / "BOOK.PRE_EDITION.md"
        pre_edition.parent.mkdir(parents=True, exist_ok=True)
        pre_edition.write_text(
            "# Chapter 01\n\n![](assets/images/ch01_01.jpg)\n\nOfficial source.\n",
            encoding="utf-8",
        )
        approved = md_quality.approve_md_final(self.edition)
        final_path = Path(approved["path"])
        self.assertEqual(resolve_miolo_source_path(self.edition), final_path)
        final_path.write_text("tampered derivative", encoding="utf-8")
        self.assertEqual(resolve_miolo_source_path(self.edition), official_body.canonical_path(self.edition))

    def test_db_committed_failure_is_reconciled(self):
        prior = official_body.promote(
            self.edition,
            b"CHAPTER 1\n\nPrior.\n",
            provenance="internal_polish",
            source_stage="polish",
        )
        old_payload = official_body.canonical_path(self.edition).read_bytes()
        real_write = intake_storage.atomic_write_bytes
        calls = {"count": 0}

        def fail_publish(path, payload, **kwargs):
            calls["count"] += 1
            if calls["count"] == 3:
                raise OSError("simulated canonical publish failure")
            return real_write(path, payload, **kwargs)

        with patch("gaiden.application.pipeline.official_body.intake_storage.atomic_write_bytes", side_effect=fail_publish):
            with self.assertRaises(OSError):
                official_body.promote(
                    self.edition,
                    b"CHAPTER 1\n\nRecovered new official.\n",
                    provenance="internal_polish",
                    source_stage="polish",
                )
        self.assertEqual(official_body.canonical_path(self.edition).read_bytes(), old_payload)
        operation = OfficialBodyPromotion.objects.exclude(state="COMPLETED").get()
        self.assertEqual(operation.state, OfficialBodyPromotion.DB_COMMITTED)
        self.assertEqual(official_body.reconcile_operation(operation), "completed")
        self.assertIsNotNone(official_body.resolve_official_body(self.edition))
        self.assertFalse(OfficialBodySnapshot.objects.get(pk=prior.snapshot_id).is_active)

    def test_failure_before_database_commit_preserves_previous_official(self):
        previous = official_body.promote(
            self.edition,
            b"CHAPTER 1\n\nPrevious official.\n",
            provenance="internal_polish",
            source_stage="polish",
        )
        previous_payload = official_body.canonical_path(self.edition).read_bytes()
        real_write = intake_storage.atomic_write_bytes
        calls = {"count": 0}

        def fail_version_write(path, payload, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("simulated pre-commit failure")
            return real_write(path, payload, **kwargs)

        with patch(
            "gaiden.application.pipeline.official_body.intake_storage.atomic_write_bytes",
            side_effect=fail_version_write,
        ):
            with self.assertRaisesRegex(OSError, "pre-commit"):
                official_body.promote(
                    self.edition,
                    b"CHAPTER 1\n\nUncommitted replacement.\n",
                    provenance="internal_polish",
                    source_stage="polish",
                )
        self.assertEqual(official_body.canonical_path(self.edition).read_bytes(), previous_payload)
        self.assertEqual(
            OfficialBodySnapshot.objects.get(edition=self.edition, is_active=True).pk,
            previous.snapshot_id,
        )

    def test_missing_or_divergent_official_body_blocks_completion(self):
        official_body.promote(
            self.edition,
            b"CHAPTER 1\n\nValidated official.\n",
            provenance="internal_polish",
            source_stage="polish",
        )
        pipeline = EditionPipeline.objects.get(edition=self.edition)
        self.assertTrue(resolve_block_two_completion(self.edition, pipeline).done)
        official_body.canonical_path(self.edition).write_text("tampered", encoding="utf-8")
        self.assertFalse(resolve_block_two_completion(self.edition, pipeline).done)
        official_body.canonical_path(self.edition).unlink()
        self.assertFalse(resolve_block_two_completion(self.edition, pipeline).done)

    def test_mutating_endpoints_reject_get(self):
        self.assertEqual(
            self.client.get(reverse("pipeline_translation_drive_upload", kwargs={"edition_id": self.edition.id})).status_code,
            405,
        )
        self.assertEqual(
            self.client.get(reverse("pipeline_drive_return_promote", kwargs={"edition_id": self.edition.id})).status_code,
            405,
        )
        self.assertEqual(
            self.client.get(reverse("save_merge_polidor_preview", kwargs={"edition_id": self.edition.id})).status_code,
            405,
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    "pipeline_drive_return_confirm_warning",
                    kwargs={"edition_id": self.edition.id},
                )
            ).status_code,
            405,
        )

    def test_mutating_endpoint_keeps_csrf_protection(self):
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(
            reverse(
                "pipeline_drive_return_confirm_warning",
                kwargs={"edition_id": self.edition.id},
            ),
            {"confirmation_note": "reviewed"},
        )
        self.assertEqual(response.status_code, 403)

    def test_steps_page_does_not_disclose_absolute_storage_root(self):
        official_body.promote(
            self.edition,
            b"CHAPTER 1\n\nOfficial source.\n",
            provenance="internal_polish",
            source_stage="polish",
        )
        response = self.client.get(
            reverse("edition_steps", kwargs={"edition_id": self.edition.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, str(self.storage_root))

    def test_book_code_and_edition_links_are_unique(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                IntakeItem.objects.create(
                    batch=self.batch,
                    order_index=2,
                    source_filename="other.txt",
                    source_format="txt",
                    book_code=self.item.book_code,
                )

    def test_duplicate_detection_is_global_across_batches(self):
        other_batch = IntakeBatch.objects.create(
            code="job-v2-other",
            name="Other batch",
            source_language="en",
        )
        duplicate = IntakeItem.objects.create(
            batch=other_batch,
            order_index=1,
            source_filename="tarzan-copy.txt",
            source_format="txt",
            source_sha256=self.item.source_sha256,
        )
        canonical_id = update_duplicate_group(other_batch, self.item.source_sha256)
        duplicate.refresh_from_db()
        self.assertEqual(canonical_id, self.item.id)
        self.assertEqual(duplicate.duplicate_of_id, self.item.id)


class BookCodeConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.batch = IntakeBatch.objects.create(code="codes", name="Codes", source_language="en")
        self.items = [
            IntakeItem.objects.create(
                batch=self.batch,
                order_index=index,
                source_filename=f"book-{index}.txt",
                source_format="txt",
            )
            for index in (1, 2)
        ]

    def test_concurrent_allocation_does_not_duplicate_book_code(self):
        barrier = threading.Barrier(2)

        def allocate(item_id):
            close_old_connections()
            item = IntakeItem.objects.get(pk=item_id)
            barrier.wait()
            code = assign_book_code(item)
            close_old_connections()
            return code

        with ThreadPoolExecutor(max_workers=2) as executor:
            codes = list(executor.map(allocate, [item.id for item in self.items]))
        self.assertEqual(len(set(codes)), 2)
        self.assertEqual(set(codes), {"book_0001", "book_0002"})
