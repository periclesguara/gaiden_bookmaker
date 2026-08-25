from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from editorial.models import Contributor, Edition, Language, PipelineArtifact, Seal, Work
from gaiden.application.normalization import CONTRACT_VERSION
from gaiden.infrastructure import storage
from pipeline.models import BookEditionTemplate, ManualTranslationJob, TranslationUnit
from pipeline.services import block01_normalize, chapter_translation


class ContractClassifier:
    model = "Qwen/test-block01"

    def classify(self, *, source_sha256, blocks):
        decisions = []
        for block in blocks:
            text = str(block["text"])
            lower = text.casefold()
            decision = "KEEP_BODY"
            family = "none"
            if "project gutenberg" in lower:
                decision = "DROP_PLATFORM_LICENSE" if "license" in lower else "DROP_PLATFORM_METADATA"
                family = "project_gutenberg"
            row = {
                "block_id": block["block_id"],
                "start_offset": block["start_offset"],
                "end_offset": block["end_offset"],
                "decision": decision,
                "source_family": family,
                "confidence": 0.99,
                "evidence": text.strip()[:120] or "separator",
            }
            heading = text.strip()
            if heading.upper().startswith("CHAPTER"):
                row.update(
                    decision="KEEP_HEADING",
                    source_family="none",
                    heading_level=1,
                    heading_type="chapter",
                    heading_text=heading,
                )
            decisions.append(row)
        return {"schema": CONTRACT_VERSION, "source_sha256": source_sha256, "blocks": decisions}


@override_settings(
    GAIDEN_DRIVE_REMOTE="gaiden_drive",
    GAIDEN_CHAPTER_SPLIT_ALERT_CHARACTERS=30_000,
    GAIDEN_CHAPTER_SPLIT_HARD_LIMIT_CHARACTERS=60_000,
    GAIDEN_CHAPTER_SPLIT_QWEN_CONFIDENCE=0.85,
)
class Block01NormalizeIntegrationTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gaiden-block01-")
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        language = Language.objects.create(code="en", name="English", native_name="English")
        author = Contributor.objects.create(name="Jane Austen", role="AUTHOR")
        seal = Seal.objects.create(slug="mantaquest-block01", name="MantaQuest")
        self.work = Work.objects.create(
            code="book_7009",
            title="Emma",
            original_language=language,
            author=author,
            source_provenance={
                "fields": {
                    "original_title": {
                        "value": "Human Emma",
                        "source": "manual",
                        "status": "edited",
                    }
                }
            },
        )
        self.edition = Edition.objects.create(
            work=self.work,
            language=language,
            seal=seal,
            title="Emma",
            author="Jane Austen",
            translator="Existing Translator",
            adapter="Existing Adapter",
        )
        self.raw = Path(self.temp.name) / "raw" / "book_7009" / "Emma.txt"
        self.raw.parent.mkdir(parents=True)
        self.raw_bytes = (
            "Title: Emma\nAuthor: Jane Austen\nProject Gutenberg ebook 158\n\n"
            "Project Gutenberg license terms\n\n"
            "CHAPTER I\n\nEmma Woodhouse lived at Hartfield.\n\n"
            "CHAPTER II\n\nThe story continued.\n"
        ).encode("utf-8")
        self.raw.write_bytes(self.raw_bytes)
        self.template = BookEditionTemplate.objects.create(
            book_code=self.work.code,
            language="en",
            title="Emma",
            author_name="Jane Austen",
            publication_year=1815,
            source_original_name="Emma.txt",
            source_saved_path=str(self.raw),
            source_file_size=len(self.raw_bytes),
            source_file_sha256=hashlib.sha256(self.raw_bytes).hexdigest(),
            source_uploaded_by="Automated Intake",
        )
        self.client = Client()

    def normalize(self):
        return block01_normalize.run_normalize(
            edition=self.edition,
            source_template=self.template,
            classifier=ContractClassifier(),
        )

    def test_normalize_preserves_raw_and_writes_canonical_artifacts(self):
        edition_before = (self.edition.title, self.edition.author, self.edition.translator, self.edition.adapter)
        result = self.normalize()
        self.assertEqual(self.raw.read_bytes(), self.raw_bytes)
        self.assertEqual(Path(result["normalized_path"]).name, "normalized_body.txt")
        self.assertEqual(Path(result["manifest_path"]).name, "normalize-manifest.json")
        self.assertEqual(Path(result["structure_map_path"]).name, "structure-map.json")
        self.assertNotIn("Project Gutenberg", Path(result["normalized_path"]).read_text())
        self.assertFalse(
            PipelineArtifact.objects.filter(work_code=self.work.code, stage="heading_clean").exists()
        )
        self.assertTrue(PipelineArtifact.objects.filter(work_code=self.work.code, stage="raw").exists())
        self.assertTrue(PipelineArtifact.objects.filter(work_code=self.work.code, stage="normalize").exists())
        self.assertTrue(PipelineArtifact.objects.filter(work_code=self.work.code, stage="structure_map").exists())
        self.edition.refresh_from_db()
        self.assertEqual(
            (self.edition.title, self.edition.author, self.edition.translator, self.edition.adapter),
            edition_before,
        )

    def test_reexecution_preserves_human_provenance(self):
        self.normalize()
        self.normalize()
        self.work.refresh_from_db()
        field = self.work.source_provenance["fields"]["original_title"]
        self.assertEqual(field["value"], "Human Emma")
        self.assertEqual(field["source"], "manual")
        self.assertEqual(self.work.source_provenance["workflow_status"], "PROVENANCE_STAGED")

    def test_get_does_not_call_qwen_and_interface_has_exact_block01_stages(self):
        page = reverse("post_intake_workflow", kwargs={"edition_id": self.edition.id})
        with patch("pipeline.views.QwenBlockClassifier.from_env") as qwen:
            response = self.client.get(page)
        self.assertEqual(response.status_code, 200)
        qwen.assert_not_called()
        content = response.content.decode("utf-8")
        for label in (
            "01 · Normalize — Qwen + JSON",
            "02 · Split by Chapter",
            "03 · Google Drive",
            "04 · Tradução por capítulos",
            "05 · Return e Merge",
        ):
            self.assertIn(label, content)
        self.assertNotIn("Headings Cleaner", content)

        edition_page = self.client.get(
            reverse("edition_steps", kwargs={"edition_id": self.edition.id})
        )
        self.assertEqual(edition_page.status_code, 200)
        edition_content = edition_page.content.decode("utf-8")
        self.assertIn("Bloco 01 · Miolo traduzido", edition_content)
        self.assertIn("01 · Normalize — Qwen + JSON", edition_content)
        self.assertNotIn("HeadingCleaner (Mechanical)", edition_content)
        self.assertNotIn("Gate heading_cleaner", edition_content)

    def test_new_job_consumes_normalized_body_and_finishes_block01(self):
        self.normalize()
        job = chapter_translation.prepare_chapter_job(
            edition=self.edition,
            target_edition=self.edition,
            target_language="en_us",
            translation_mode=ManualTranslationJob.MODE_MODERNIZE_2026,
            source_path=storage.normalized_body_path(self.work.code, "en"),
            structure_map_path=storage.structure_map_path(self.work.code, "en"),
        )
        self.assertEqual(job.schema_version, chapter_translation.JOB_SCHEMA_V3)
        self.assertEqual(job.source_artifact.stage, "normalize")
        self.assertEqual(job.status, ManualTranslationJob.STATUS_SPLIT_VALIDATED)
        source_text = Path(job.source_path).read_text(encoding="utf-8")
        return_dir = (
            Path(self.temp.name)
            / "translation_jobs"
            / self.work.code
            / "en_us"
            / "return"
            / "chapters"
        )
        return_dir.mkdir(parents=True, exist_ok=True)
        for unit in job.units.order_by("sequence"):
            data = source_text[unit.source_start_offset : unit.source_end_offset].encode("utf-8")
            (return_dir / unit.expected_return_filename).write_bytes(data)
            unit.return_sha256 = hashlib.sha256(data).hexdigest()
            unit.status = TranslationUnit.STATUS_VALIDATED
            unit.save(update_fields=["return_sha256", "status"])
        result = chapter_translation.merge_chapter_returns(job)
        job.refresh_from_db()
        self.assertEqual(result["status"], ManualTranslationJob.STATUS_BLOCK_01_COMPLETE)
        self.assertEqual(job.status, ManualTranslationJob.STATUS_BLOCK_01_COMPLETE)
        root = Path(self.temp.name) / "translation_jobs" / self.work.code / "en_us"
        self.assertTrue((root / "translated_body.txt").is_file())
        self.assertTrue((root / "translation-manifest.json").is_file())
        self.assertTrue((root / "qa-report.json").is_file())

    def test_frontmatter_is_blocked_until_block01_complete(self):
        self.normalize()
        response = self.client.get(
            reverse("frontmatter_template_edit", kwargs={"book_code": self.work.code, "language": "en"})
        )
        self.assertEqual(response.status_code, 302)

        ManualTranslationJob.objects.create(
            edition=self.edition,
            target_edition=self.edition,
            job_id=f"{self.work.code}__en_us",
            source_language="en",
            target_language="en_us",
            translation_mode=ManualTranslationJob.MODE_MODERNIZE_2026,
            schema_version=chapter_translation.JOB_SCHEMA_V3,
            drive_path="04_TRANSLATION_JOBS/book_7009/en_us",
            source_path=str(storage.normalized_body_path(self.work.code, "en")),
            source_sha256="a" * 64,
            expected_return_name="book_7009_en_us_translated.txt",
            status=ManualTranslationJob.STATUS_SPLIT_VALIDATED,
        )
        response = self.client.get(
            reverse("frontmatter_template_edit", kwargs={"book_code": self.work.code, "language": "en"})
        )
        self.assertEqual(response.status_code, 302)

    def test_normalize_manifest_keeps_traceability_chain(self):
        result = self.normalize()
        manifest = json.loads(Path(result["manifest_path"]).read_text())
        provenance = Work.objects.get(pk=self.work.pk).source_provenance
        self.assertEqual(manifest["raw"]["sha256"], hashlib.sha256(self.raw_bytes).hexdigest())
        self.assertEqual(manifest["correlation_id"], provenance["normalize_correlation_id"])
        self.assertEqual(provenance["technical"]["book_code"], self.work.code)
        self.assertEqual(provenance["technical"]["source_size_bytes"], len(self.raw_bytes))
