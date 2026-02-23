from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from editorial.models import Contributor, ContributorRole, Edition, Language, Seal, Work
from pipeline.services import canonical_index


class CanonicalIndexFlowTests(TestCase):
    def _make_edition(self) -> Edition:
        lang = Language.objects.create(code="en", name="English", native_name="English", is_active=True)
        seal = Seal.objects.create(slug="mantaquest", name="MantaQuest", is_active=True)
        author = Contributor.objects.create(name="Arthur Conan Doyle", role=ContributorRole.AUTHOR)
        work = Work.objects.create(
            code="book_0005",
            title="Test Book",
            original_language=lang,
            author=author,
            publisher="MantaQuest",
            source_format="TXT",
        )
        edition = Edition.objects.create(work=work, language=lang, seal=seal, status=Edition.STATUS_REGISTERED)
        return edition

    def test_materialize_raw_writes_canonical_source_and_updates_index(self):
        edition = self._make_edition()
        upload = SimpleUploadedFile("source.txt", b"line 1\nline 2\n", content_type="text/plain")

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            with override_settings(MEDIA_ROOT=str(root / "media")):
                edition.raw_upload = upload
                edition.status = Edition.STATUS_UPLOADED
                edition.save(update_fields=["raw_upload", "status", "updated_at"])
                with patch.object(canonical_index, "project_root", return_value=root), patch.object(
                    canonical_index, "_git_text", return_value="ok"
                ):
                    result = canonical_index.materialize_raw(edition)
                edition.refresh_from_db()
                self.assertEqual(edition.status, Edition.STATUS_INGESTED)
                self.assertTrue(edition.raw_materialized_path.endswith("data/raw/book_0005/en/source.txt"))
                self.assertEqual(result["raw_materialized_path"], edition.raw_materialized_path)
                self.assertEqual(len(edition.raw_sha256), 64)
                self.assertTrue((root / edition.raw_materialized_path).exists())
                self.assertTrue((root / result["canonical_run_dir"] / "manifest.json").exists())

    def test_freeze_canonical_writes_truth_and_receipts(self):
        edition = self._make_edition()
        edition.book_id = "book_0005"
        edition.lang = "en"
        edition.status = Edition.STATUS_INGESTED
        edition.save(update_fields=["book_id", "lang", "status", "updated_at"])

        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "data" / "canonical" / "book_0005" / "en" / "canonical.md"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("Final canonical text\n", encoding="utf-8")

            with patch.object(canonical_index, "project_root", return_value=root), patch.object(
                canonical_index, "_git_text", return_value="ok"
            ):
                result = canonical_index.freeze_canonical(edition)
            edition.refresh_from_db()
            self.assertEqual(edition.status, Edition.STATUS_CANONICAL_READY)
            self.assertTrue(edition.truth_path.endswith("data/books/book_0005/en/book_0005_refine_clean.md"))
            self.assertEqual(result["truth_path"], edition.truth_path)
            self.assertEqual(len(edition.truth_sha256), 64)
            self.assertTrue((root / edition.truth_path).exists())
            self.assertTrue((root / result["canonical_run_dir"] / "SHA256SUMS.txt").exists())
            self.assertTrue((root / result["canonical_run_dir"] / "manifest.json").exists())
