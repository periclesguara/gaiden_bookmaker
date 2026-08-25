from __future__ import annotations

import json
import hashlib
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from editorial.models import Contributor, Edition, Language, PipelineArtifact, Seal, Work
from gaiden.infrastructure import storage
from pipeline.models import BookEditionTemplate, ManualTranslationJob, TranslationJobEvent, TranslationUnit
from pipeline.services import chapter_translation, manual_translation


class FakeChapterGateway:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.publish_count = 0
        self.fail_next_publish = False

    def publish_bytes(self, relative_path: str, data: bytes) -> None:
        if self.fail_next_publish:
            self.fail_next_publish = False
            raise OSError("temporary Drive failure")
        self.files[relative_path] = data
        self.publish_count += 1

    def read_bytes(self, relative_path: str) -> bytes:
        if relative_path not in self.files:
            raise OSError(f"missing {relative_path}")
        return self.files[relative_path]

    def stat(self, relative_path: str):
        if relative_path == "":
            return {"ID": "root-folder"}
        if relative_path in self.files:
            return {"ID": f"file-{relative_path}"}
        prefix = relative_path.rstrip("/") + "/"
        if any(name.startswith(prefix) for name in self.files):
            return {"ID": f"folder-{relative_path}"}
        return None

    def list_files(self, relative_directory: str):
        prefix = relative_directory.rstrip("/") + "/"
        return [
            {"Name": name[len(prefix) :], "ID": f"return-{index}"}
            for index, name in enumerate(sorted(self.files), start=1)
            if name.startswith(prefix) and "/" not in name[len(prefix) :]
        ]


@override_settings(
    GAIDEN_DRIVE_REMOTE="gaiden_drive",
    GAIDEN_CHAPTER_SPLIT_ALERT_CHARACTERS=30_000,
    GAIDEN_CHAPTER_SPLIT_HARD_LIMIT_CHARACTERS=60_000,
    GAIDEN_CHAPTER_SPLIT_QWEN_CONFIDENCE=0.85,
    GAIDEN_CHAPTER_RETURN_MIN_SIZE_RATIO=0.45,
    GAIDEN_CHAPTER_RETURN_MAX_SIZE_RATIO=1.80,
    GAIDEN_CHAPTER_RETURN_MIN_PARAGRAPH_RATIO=0.50,
    GAIDEN_CHAPTER_RETURN_MAX_PARAGRAPH_RATIO=2.00,
)
class ChapterTranslationWorkflowTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gaiden-chapter-translation-")
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        language = Language.objects.create(code="en", name="English", native_name="English")
        author = Contributor.objects.create(name="Jane Example", role="AUTHOR")
        seal = Seal.objects.create(slug="mantaquest", name="MantaQuest")
        self.work = Work.objects.create(
            code="book_7007",
            title="Example Novel",
            original_language=language,
            author=author,
            source_provenance={"platform": "Project Gutenberg", "identifier": "7007"},
        )
        self.edition = Edition.objects.create(work=self.work, language=language, seal=seal)
        self.heading_path = Path(self.temp.name) / "chunks" / "book_7007" / "heading_cleaner" / "clean.txt"
        self.heading_path.parent.mkdir(parents=True, exist_ok=True)
        self.heading_text = (
            "Title and dedication.\n\n"
            "CHAPTER I\n\nAlice met Darcy. Alice answered Darcy.\n\n"
            "CHAPTER II\n\nDarcy met Alice. Darcy answered Alice.\n"
        )
        self.heading_path.write_text(self.heading_text, encoding="utf-8")
        self.gateway = FakeChapterGateway()

    def prepare(self, **kwargs):
        return chapter_translation.prepare_chapter_job(
            edition=self.edition,
            target_edition=self.edition,
            target_language="en_us",
            translation_mode=ManualTranslationJob.MODE_MODERNIZE_2026,
            source_path=self.heading_path,
            **kwargs,
        )

    def export(self, job=None):
        return chapter_translation.export_chapter_job(job or self.prepare(), gateway=self.gateway)

    def return_all_source_units(self, job):
        self.add_return_manifest(job)
        for unit in job.units.order_by("sequence"):
            source = self.heading_text[unit.source_start_offset : unit.source_end_offset].encode("utf-8")
            self.gateway.files[f"return/chapters/{unit.expected_return_filename}"] = source

    def add_return_manifest(self, job, **overrides):
        payload = {
            "schema": "gaiden_manual_translation_return_v2",
            "job_id": job.job_id,
            "book_code": job.edition.work.code,
            "target_language": job.target_language,
            "source_sha256": job.source_sha256,
            "units": [
                {
                    "unit_id": unit.unit_id,
                    "expected_return_file": f"return/chapters/{unit.expected_return_filename}",
                    "return_sha256": "",
                }
                for unit in job.units.order_by("sequence")
            ],
        }
        payload.update(overrides)
        self.gateway.files["return/translation-return.json"] = (
            json.dumps(payload) + "\n"
        ).encode("utf-8")

    def test_job_creation_is_idempotent_and_indexes_heading_clean_artifact(self):
        first = self.prepare()
        second = self.prepare()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.schema_version, chapter_translation.JOB_SCHEMA_V2)
        self.assertEqual(first.status, ManualTranslationJob.STATUS_SPLIT_VALIDATED)
        self.assertEqual(first.units.count(), 3)
        artifact = PipelineArtifact.objects.get(pk=first.source_artifact_id)
        self.assertEqual(artifact.stage, "heading_clean")
        self.assertEqual(artifact.sha256, first.source_sha256)

    def test_explicit_resplit_replaces_only_pre_drive_units(self):
        job = self.prepare()
        old_units = list(job.units.values_list("source_text_sha256", flat=True))
        self.heading_path.write_text(self.heading_text.replace("answered", "replied"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Refazer split"):
            self.prepare()
        replaced = self.prepare(force=True)
        self.assertNotEqual(old_units, list(replaced.units.values_list("source_text_sha256", flat=True)))
        self.export(replaced)
        with self.assertRaisesRegex(ValueError, "depois do início"):
            self.prepare(force=True)

    def test_v1_job_remains_on_legacy_path(self):
        legacy = ManualTranslationJob.objects.create(
            edition=self.edition,
            target_edition=self.edition,
            source_language="en",
            target_language="en_us",
            drive_path="gaiden_drive:04_TRANSLATION_JOBS/book_7007/en-us",
            source_path=str(self.heading_path),
            source_sha256="a" * 64,
            expected_return_name="book_7007_en_us_translated.txt",
        )
        self.assertEqual(legacy.schema_version, "gaiden_manual_translation_job_v1")
        with self.assertRaisesRegex(ValueError, "job v1"):
            self.prepare()
        capture = FakeChapterGateway()
        result = manual_translation.export_job(
            book_code="book_7010",
            title="Legacy",
            author="Author",
            source_language="en",
            target_language="en_us",
            source_path=self.heading_path,
            publisher=capture,
        )
        payload = json.loads(capture.files["input/translation-job.json"])
        self.assertEqual(payload["schema"], "gaiden_manual_translation_job_v1")
        self.assertEqual(result["expected_return_name"], "book_7010_en_us_translated.txt")

    def test_export_creates_v2_contract_style_and_chapter_directories_only_for_requested_language(self):
        job = self.prepare()
        result = self.export(job)
        self.assertEqual(result["status"], "DRIVE_READY")
        self.assertIn("input/translation-job.json", self.gateway.files)
        self.assertIn("input/style-contract.json", self.gateway.files)
        self.assertIn("return/RETURN_HERE.txt", self.gateway.files)
        self.assertIn("return/translation-return.template.json", self.gateway.files)
        self.assertEqual(len([name for name in self.gateway.files if name.startswith("input/chapters/")]), 3)
        contract = json.loads(self.gateway.files["input/translation-job.json"])
        self.assertEqual(contract["schema"], chapter_translation.JOB_SCHEMA_V2)
        self.assertEqual(contract["target_language"], "en_us")
        self.assertEqual(contract["source"]["sha256"], job.source_sha256)
        self.assertFalse(any("pt-br" in name or "/fr/" in name for name in self.gateway.files))

    def test_export_is_idempotent(self):
        job = self.prepare()
        first = self.export(job)
        publish_count = self.gateway.publish_count
        job.refresh_from_db()
        second = self.export(job)
        self.assertEqual(first["status"], "DRIVE_READY")
        self.assertEqual(second["status"], "NO_OP")
        self.assertEqual(self.gateway.publish_count, publish_count)

    def test_export_rejects_divergent_existing_drive_file(self):
        job = self.prepare()
        unit = job.units.order_by("sequence").first()
        self.gateway.files[f"input/chapters/{unit.input_filename}"] = b"divergent"
        with self.assertRaises(FileExistsError):
            self.export(job)
        job.refresh_from_db()
        self.assertEqual(job.status, ManualTranslationJob.STATUS_CONFLICT)

    def test_export_can_resume_after_retryable_transport_failure(self):
        job = self.prepare()
        self.gateway.fail_next_publish = True
        with self.assertRaises(OSError):
            self.export(job)
        job.refresh_from_db()
        self.assertEqual(job.status, ManualTranslationJob.STATUS_FAILED_RETRYABLE)
        result = self.export(job)
        self.assertEqual(result["status"], "DRIVE_READY")

    def test_export_rejects_changed_heading_clean_source_hash(self):
        job = self.prepare()
        self.heading_path.write_text(self.heading_text + "changed", encoding="utf-8")
        with self.assertRaisesRegex(Exception, "alterado"):
            self.export(job)

    def test_partial_return_is_recorded_and_missing_units_remain_visible(self):
        job = self.prepare()
        self.export(job)
        first = job.units.order_by("sequence").first()
        source = self.heading_text[first.source_start_offset : first.source_end_offset].encode("utf-8")
        self.add_return_manifest(job)
        self.gateway.files[f"return/chapters/{first.expected_return_filename}"] = source
        result = chapter_translation.discover_chapter_returns(job, gateway=self.gateway)
        job.refresh_from_db()
        progress = chapter_translation.job_progress(job)
        self.assertEqual(result["returned"], 1)
        self.assertEqual(job.status, ManualTranslationJob.STATUS_PARTIAL_RETURN)
        self.assertEqual(len(progress["missing"]), 2)
        with self.assertRaisesRegex(ValueError, "100%"):
            chapter_translation.merge_chapter_returns(job)

    def test_return_name_with_path_traversal_is_rejected(self):
        job = self.prepare()
        self.export(job)
        self.gateway.list_files = lambda relative_directory: [
            {"Name": "../0001__chapter_01__en_us.txt", "ID": "unsafe"}
        ]
        with self.assertRaisesRegex(ValueError, "inseguro"):
            chapter_translation.discover_chapter_returns(job, gateway=self.gateway)

    def test_duplicate_return_name_in_one_listing_is_rejected(self):
        job = self.prepare()
        self.export(job)
        first = job.units.order_by("sequence").first()
        self.add_return_manifest(job)
        name = first.expected_return_filename
        self.gateway.list_files = lambda relative_directory: [
            {"Name": name, "ID": "duplicate-a"},
            {"Name": name, "ID": "duplicate-b"},
        ]
        with self.assertRaisesRegex(ValueError, "duplicados"):
            chapter_translation.discover_chapter_returns(job, gateway=self.gateway)

    def test_same_return_hash_is_not_imported_twice(self):
        job = self.prepare()
        self.export(job)
        first = job.units.order_by("sequence").first()
        source = self.heading_text[first.source_start_offset : first.source_end_offset].encode("utf-8")
        self.add_return_manifest(job)
        self.gateway.files[f"return/chapters/{first.expected_return_filename}"] = source
        chapter_translation.discover_chapter_returns(job, gateway=self.gateway)
        result = chapter_translation.discover_chapter_returns(job, gateway=self.gateway)
        self.assertEqual(result["noop"], 1)

    def test_divergent_second_return_marks_unit_conflict(self):
        job = self.prepare()
        self.export(job)
        first = job.units.order_by("sequence").first()
        path = f"return/chapters/{first.expected_return_filename}"
        self.add_return_manifest(job)
        self.gateway.files[path] = self.heading_text[first.source_start_offset : first.source_end_offset].encode()
        chapter_translation.discover_chapter_returns(job, gateway=self.gateway)
        self.gateway.files[path] = b"A divergent second return."
        result = chapter_translation.discover_chapter_returns(job, gateway=self.gateway)
        first.refresh_from_db()
        self.assertEqual(result["conflicts"], 1)
        self.assertEqual(first.status, TranslationUnit.STATUS_CONFLICT)

    def test_unknown_or_wrong_language_return_name_is_rejected(self):
        job = self.prepare()
        self.export(job)
        self.add_return_manifest(job)
        self.gateway.files["return/chapters/0001__chapter_01__ptbr.txt"] = b"Wrong language"
        with self.assertRaisesRegex(ValueError, "desconhecidos"):
            chapter_translation.discover_chapter_returns(job, gateway=self.gateway)
        job.refresh_from_db()
        self.assertEqual(job.status, ManualTranslationJob.STATUS_CONFLICT)

    def test_empty_return_is_rejected(self):
        job = self.prepare()
        self.export(job)
        first = job.units.order_by("sequence").first()
        self.add_return_manifest(job)
        self.gateway.files[f"return/chapters/{first.expected_return_filename}"] = b"\n"
        result = chapter_translation.discover_chapter_returns(job, gateway=self.gateway)
        first.refresh_from_db()
        self.assertEqual(result["conflicts"], 1)
        self.assertEqual(first.status, TranslationUnit.STATUS_REJECTED)

    def test_return_manifest_with_wrong_source_hash_is_rejected(self):
        job = self.prepare()
        self.export(job)
        first = job.units.order_by("sequence").first()
        self.add_return_manifest(job, source_sha256="f" * 64)
        self.gateway.files[f"return/chapters/{first.expected_return_filename}"] = b"Returned content."
        with self.assertRaisesRegex(ValueError, "source_sha256"):
            chapter_translation.discover_chapter_returns(job, gateway=self.gateway)
        job.refresh_from_db()
        self.assertEqual(job.status, ManualTranslationJob.STATUS_CONFLICT)

    def test_return_manifest_declared_unit_hash_must_match_file(self):
        job = self.prepare()
        self.export(job)
        first = job.units.order_by("sequence").first()
        self.add_return_manifest(job)
        manifest = json.loads(self.gateway.files["return/translation-return.json"])
        manifest["units"][0]["return_sha256"] = "f" * 64
        self.gateway.files["return/translation-return.json"] = json.dumps(manifest).encode("utf-8")
        self.gateway.files[f"return/chapters/{first.expected_return_filename}"] = b"Returned content."
        result = chapter_translation.discover_chapter_returns(job, gateway=self.gateway)
        first.refresh_from_db()
        self.assertEqual(result["conflicts"], 1)
        self.assertEqual(first.status, TranslationUnit.STATUS_CONFLICT)
        self.assertIn("declared_return_hash_mismatch", first.validation_report["errors"])

    def test_validation_rejects_model_message_and_blocks_merge(self):
        job = self.prepare()
        self.export(job)
        self.return_all_source_units(job)
        chapter_translation.discover_chapter_returns(job, gateway=self.gateway)
        first = job.units.order_by("sequence").first()
        target = Path(self.temp.name) / "translation_jobs" / "book_7007" / "en_us" / "return" / "chapters" / first.expected_return_filename
        bad = b"Here is the translated chapter.\n"
        target.write_bytes(bad)
        first.return_sha256 = __import__("hashlib").sha256(bad).hexdigest()
        first.save(update_fields=["return_sha256"])
        report = chapter_translation.validate_chapter_returns(job)
        self.assertIn("model_message_or_refusal", report["units"][0]["errors"])
        with self.assertRaisesRegex(ValueError, "100%"):
            chapter_translation.merge_chapter_returns(job)

    def test_rejected_unit_can_resume_with_a_new_audited_return(self):
        job = self.prepare()
        self.export(job)
        self.return_all_source_units(job)
        first = job.units.order_by("sequence").first()
        remote_path = f"return/chapters/{first.expected_return_filename}"
        self.gateway.files[remote_path] = b"Here is the translated chapter.\n"
        chapter_translation.discover_chapter_returns(job, gateway=self.gateway)
        chapter_translation.validate_chapter_returns(job)
        first.refresh_from_db()
        self.assertEqual(first.status, TranslationUnit.STATUS_REJECTED)
        corrected = self.heading_text[first.source_start_offset : first.source_end_offset].encode("utf-8")
        self.gateway.files[remote_path] = corrected
        chapter_translation.discover_chapter_returns(job, gateway=self.gateway)
        first.refresh_from_db()
        self.assertEqual(first.status, TranslationUnit.STATUS_RETURNED)
        report = chapter_translation.validate_chapter_returns(job)
        self.assertEqual(report["validated_count"], report["unit_count"])
        archived = (
            Path(self.temp.name)
            / "translation_jobs"
            / "book_7007"
            / "en_us"
            / "return"
            / "rejected"
        )
        self.assertTrue(any(archived.glob(f"{first.unit_id}__*.txt")))

    def test_merge_is_manifest_ordered_idempotent_and_preserves_provenance(self):
        provenance_before = dict(self.work.source_provenance)
        job = self.prepare()
        self.export(job)
        self.return_all_source_units(job)
        chapter_translation.discover_chapter_returns(job, gateway=self.gateway)
        report = chapter_translation.validate_chapter_returns(job)
        self.assertEqual(report["validated_count"], 3)
        progress = chapter_translation.job_progress(job)
        self.assertTrue(progress["can_merge"])
        result = chapter_translation.merge_chapter_returns(job)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(Path(result["path"]).read_text(encoding="utf-8"), self.heading_text)
        job.refresh_from_db()
        repeated = chapter_translation.merge_chapter_returns(job)
        self.assertEqual(repeated["status"], "NO_OP")
        self.assertEqual(job.status, ManualTranslationJob.STATUS_COMPLETED)
        self.assertTrue(job.final_sha256)
        self.assertEqual(job.final_artifact.sha256, job.final_sha256)
        self.work.refresh_from_db()
        self.assertEqual(self.work.source_provenance, provenance_before)
        self.assertGreaterEqual(TranslationJobEvent.objects.filter(translation_job=job).count(), 8)


@override_settings(
    GAIDEN_DRIVE_REMOTE="gaiden_drive",
    GAIDEN_CHAPTER_SPLIT_ALERT_CHARACTERS=30_000,
    GAIDEN_CHAPTER_SPLIT_HARD_LIMIT_CHARACTERS=60_000,
    GAIDEN_CHAPTER_SPLIT_QWEN_CONFIDENCE=0.85,
    GAIDEN_CHAPTER_RETURN_MIN_SIZE_RATIO=0.45,
    GAIDEN_CHAPTER_RETURN_MAX_SIZE_RATIO=1.80,
    GAIDEN_CHAPTER_RETURN_MIN_PARAGRAPH_RATIO=0.50,
    GAIDEN_CHAPTER_RETURN_MAX_PARAGRAPH_RATIO=2.00,
)
class ChapterTranslationInterfaceTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gaiden-chapter-ui-")
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        language = Language.objects.create(code="en", name="English", native_name="English")
        author = Contributor.objects.create(name="Jane Example", role="AUTHOR")
        seal = Seal.objects.create(slug="mantaquest-ui", name="MantaQuest")
        work = Work.objects.create(
            code="book_7007",
            title="Example Novel",
            original_language=language,
            author=author,
            source_provenance={},
        )
        self.edition = Edition.objects.create(work=work, language=language, seal=seal)
        source = Path(self.temp.name) / "raw" / "source.txt"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("RAW remains unchanged", encoding="utf-8")
        BookEditionTemplate.objects.create(
            book_code=work.code,
            language="en",
            title=work.title,
            author_name=author.name,
            publication_year=2026,
            source_saved_path=str(source),
            source_original_name="source.txt",
        )
        self.heading_path = Path(self.temp.name) / "chunks" / work.code / "heading_cleaner" / "clean.txt"
        self.heading_path.parent.mkdir(parents=True, exist_ok=True)
        self.heading_path.write_text("CHAPTER I\n\nFirst.\n\nCHAPTER II\n\nSecond.\n", encoding="utf-8")
        normalized_text = self.heading_path.read_text(encoding="utf-8")
        normalized_path = storage.normalized_body_path(work.code, "en")
        normalized_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_path.write_text(normalized_text, encoding="utf-8")
        digest = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        storage.normalize_manifest_path(work.code, "en").write_text(
            json.dumps({"validated": True, "normalized_sha256": digest, "removed_block_count": 0}),
            encoding="utf-8",
        )
        storage.structure_map_path(work.code, "en").write_text(
            json.dumps(
                {
                    "schema": "gaiden_structure_map_v1",
                    "normalized_sha256": digest,
                    "validated": True,
                    "review_required": False,
                    "structures": [
                        {
                            "sequence": 1,
                            "type": "chapter",
                            "heading_original": "CHAPTER I",
                            "start_offset": normalized_text.index("CHAPTER I"),
                            "end_offset": normalized_text.index("CHAPTER I") + len("CHAPTER I"),
                        },
                        {
                            "sequence": 2,
                            "type": "chapter",
                            "heading_original": "CHAPTER II",
                            "start_offset": normalized_text.index("CHAPTER II"),
                            "end_offset": normalized_text.index("CHAPTER II") + len("CHAPTER II"),
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.client = Client()
        self.page_url = reverse("post_intake_workflow", kwargs={"edition_id": self.edition.id})

    @patch("pipeline.views.detect_chapter_boundaries")
    @patch("pipeline.views.chapter_translation.discover_chapter_returns")
    @patch("pipeline.views.chapter_translation.export_chapter_job")
    def test_get_shows_step_025_without_calling_drive_or_qwen_actions(
        self, export_job, discover_returns, qwen_detector
    ):
        response = self.client.get(self.page_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "02 · Split by Chapter")
        self.assertContains(response, "02 · Separar e validar capítulos")
        self.assertContains(response, "SHA-256")
        self.assertContains(response, "Valide primeiro a etapa 02")
        export_job.assert_not_called()
        discover_returns.assert_not_called()
        qwen_detector.assert_not_called()

    def test_explicit_split_post_creates_units_and_enables_drive_job(self):
        response = self.client.post(
            reverse("chapter_translation_split", kwargs={"edition_id": self.edition.id}),
            {"target_language": "en_us", "translation_mode": "modernize_2026"},
        )
        self.assertEqual(response.status_code, 302)
        job = ManualTranslationJob.objects.get(edition=self.edition, target_language="en_us")
        page = self.client.get(response.url)
        self.assertEqual(job.schema_version, chapter_translation.JOB_SCHEMA_V3)
        self.assertEqual(job.units.count(), 2)
        self.assertContains(page, "03 · Criar job por capítulos no Google Drive")
        self.assertContains(page, "0001__chapter_01.txt")
        self.assertContains(page, "Executar merge")

    @patch("pipeline.views.chapter_translation.export_chapter_job")
    def test_only_explicit_export_post_calls_drive_export(self, export_job):
        job = chapter_translation.prepare_chapter_job(
            edition=self.edition,
            target_edition=self.edition,
            target_language="en_us",
            translation_mode="modernize_2026",
            source_path=self.heading_path,
        )
        get_response = self.client.get(
            reverse("chapter_translation_export", kwargs={"job_id": job.id})
        )
        self.assertEqual(get_response.status_code, 302)
        export_job.assert_not_called()
        export_job.return_value = {"status": "DRIVE_READY", "unit_count": 2}
        post_response = self.client.post(
            reverse("chapter_translation_export", kwargs={"job_id": job.id})
        )
        self.assertEqual(post_response.status_code, 302)
        export_job.assert_called_once()

    def test_legacy_v1_job_keeps_monolithic_controls(self):
        ManualTranslationJob.objects.create(
            edition=self.edition,
            target_edition=self.edition,
            source_language="en",
            target_language="en_us",
            drive_path="gaiden_drive:04_TRANSLATION_JOBS/book_7007/en-us",
            source_path=str(self.heading_path),
            source_sha256="a" * 64,
            expected_return_name="book_7007_en_us_translated.txt",
        )
        response = self.client.get(self.page_url)
        self.assertContains(response, "Compatibilidade v1")
        self.assertContains(response, "Buscar e importar miolo legado")
        self.assertNotContains(response, "Verificar retornos")

    @patch("pipeline.views.manual_translation.export_job")
    def test_legacy_export_endpoint_cannot_overwrite_v2_job(self, export_job):
        job = chapter_translation.prepare_chapter_job(
            edition=self.edition,
            target_edition=self.edition,
            target_language="en_us",
            translation_mode="modernize_2026",
            source_path=self.heading_path,
        )
        response = self.client.post(
            reverse("manual_translation_export", kwargs={"edition_id": self.edition.id}),
            {"target_language": "en_us", "confirm_replace": "1"},
        )
        self.assertEqual(response.status_code, 302)
        export_job.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.schema_version, chapter_translation.JOB_SCHEMA_V2)
        self.assertEqual(job.status, ManualTranslationJob.STATUS_SPLIT_VALIDATED)

    @patch("pipeline.views.manual_translation.read_drive_return")
    def test_legacy_import_endpoint_cannot_promote_v2_job(self, read_drive_return):
        job = chapter_translation.prepare_chapter_job(
            edition=self.edition,
            target_edition=self.edition,
            target_language="en_us",
            translation_mode="modernize_2026",
            source_path=self.heading_path,
        )
        response = self.client.post(
            reverse("manual_translation_import_drive", kwargs={"job_id": job.id})
        )
        self.assertEqual(response.status_code, 302)
        read_drive_return.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, ManualTranslationJob.STATUS_SPLIT_VALIDATED)

    def test_legacy_upload_endpoint_cannot_promote_v2_job(self):
        job = chapter_translation.prepare_chapter_job(
            edition=self.edition,
            target_edition=self.edition,
            target_language="en_us",
            translation_mode="modernize_2026",
            source_path=self.heading_path,
        )
        response = self.client.post(
            reverse("manual_translation_import_upload", kwargs={"job_id": job.id}),
            {"translated_file": SimpleUploadedFile("whole-book.txt", b"Bypass attempt")},
        )
        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.status, ManualTranslationJob.STATUS_SPLIT_VALIDATED)
        self.assertFalse(job.return_sha256)

    @patch("pipeline.views.detect_chapter_boundaries")
    def test_qwen_fallback_runs_only_on_explicit_post_and_is_revalidated(self, detector):
        self.heading_path.write_text("Opening without deterministic headings.\n", encoding="utf-8")
        job = chapter_translation.prepare_chapter_job(
            edition=self.edition,
            target_edition=self.edition,
            target_language="en_us",
            translation_mode="modernize_2026",
            source_path=self.heading_path,
        )
        self.assertEqual(job.status, ManualTranslationJob.STATUS_SPLIT_REVIEW_REQUIRED)
        get_response = self.client.get(
            reverse("chapter_translation_split_qwen", kwargs={"job_id": job.id})
        )
        self.assertEqual(get_response.status_code, 302)
        detector.assert_not_called()
        text = self.heading_path.read_text(encoding="utf-8")
        detector.return_value = {
            "schema": "gaiden_chapter_detection_v1",
            "units": [
                {
                    "sequence": 1,
                    "unit_type": "introduction",
                    "heading": "",
                    "start_offset": 0,
                    "end_offset": len(text),
                    "confidence": 0.99,
                }
            ],
        }
        post_response = self.client.post(
            reverse("chapter_translation_split_qwen", kwargs={"job_id": job.id})
        )
        self.assertEqual(post_response.status_code, 302)
        detector.assert_called_once_with(text)
        job.refresh_from_db()
        self.assertEqual(job.status, ManualTranslationJob.STATUS_SPLIT_VALIDATED)
        self.assertEqual(job.split_strategy, "qwen_supervised")
