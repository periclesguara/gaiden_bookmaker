import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from editorial import kdp_mode


class EpubValidationTests(SimpleTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.edition = SimpleNamespace(
            title="An Approved Title", subtitle="First line\nSecond line", author="A. Writer",
            language=SimpleNamespace(code="en"), language_code="en",
            work=SimpleNamespace(code="fixture", title="Source title", author=None),
            publisher="Test Press", imprint_name="Test Imprint", seal_name="Test Seal",
            adapter="", translator="", editor="", publication_year=2026,
            city="", country="", cover_filepath="",
        )
        self.path = self.root / "ebook.epub"
        self.path.write_bytes(b"fake EPUB for mocked validator")
        self.patcher = patch.object(kdp_mode, "builds_dir", return_value=self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def validator(self, messages=(), returncode=0, mutate=False, malformed=False):
        def run(command, **kwargs):
            if "--version" in command:
                return subprocess.CompletedProcess(command, 0, "EPUBCheck test", "")
            if "--json" in command:
                report = Path(command[command.index("--json") + 1])
                report.write_text("{}" if malformed else json.dumps({"messages": list(messages)}))
            if mutate:
                self.path.write_bytes(b"changed during validation")
            return subprocess.CompletedProcess(command, returncode, "complete", "")
        return run

    def test_report_is_bound_to_the_validated_bytes(self):
        with patch("subprocess.run", side_effect=self.validator()):
            self.assertEqual(kdp_mode.run_epubcheck_for_edition(self.edition), self.path)
        reports = list(self.root.glob("*.epubcheck.json"))
        self.assertEqual(len(reports), 1)
        report = json.loads(reports[0].read_text())
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["artifact_sha256"])
        self.assertEqual(report["tool_version"], "EPUBCheck test")

    def test_warning_requires_review_even_when_exit_status_is_zero(self):
        with patch("subprocess.run", side_effect=self.validator([{"severity": "WARNING"}])):
            with self.assertRaises(RuntimeError):
                kdp_mode.run_epubcheck_for_edition(self.edition)

    def test_error_in_report_cannot_be_overridden_by_success_exit_status(self):
        with patch("subprocess.run", side_effect=self.validator([{"severity": "ERROR"}])):
            with self.assertRaises(RuntimeError):
                kdp_mode.run_epubcheck_for_edition(self.edition)

    def test_incomplete_evidence_cannot_pass(self):
        with patch("subprocess.run", side_effect=self.validator(malformed=True)):
            with self.assertRaises(RuntimeError):
                kdp_mode.run_epubcheck_for_edition(self.edition)

    def test_changed_artifact_cannot_pass(self):
        with patch("subprocess.run", side_effect=self.validator(mutate=True)):
            with self.assertRaises(RuntimeError):
                kdp_mode.run_epubcheck_for_edition(self.edition)

    def test_ambiguous_artifacts_are_not_resolved_by_legacy_filename_precedence(self):
        (self.root / "BOOK.EPUB3").write_bytes(b"older EPUB")
        with patch("subprocess.run", side_effect=self.validator()) as process:
            with self.assertRaises(ValueError):
                kdp_mode.run_epubcheck_for_edition(self.edition)
        process.assert_not_called()

    def test_missing_metadata_cannot_invent_a_book_title(self):
        self.edition.title = ""
        self.edition.work.title = ""
        (self.root / "kdp_merged.md").write_text("# Chapter\n\nA paragraph.")
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as process:
            with self.assertRaises(ValueError):
                kdp_mode.build_epub_for_edition(self.edition)
        process.assert_not_called()

    def test_missing_tool_is_blocked_with_evidence(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(RuntimeError):
                kdp_mode.run_epubcheck_for_edition(self.edition)
        report = json.loads(self.path.with_name("ebook.epub.epubcheck.json").read_text())
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIsNone(report["error_count"])

    def test_timeout_with_binary_output_is_recorded_without_fabricating_pass(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("epubcheck", 120, output=b"partial")):
            with self.assertRaises(RuntimeError):
                kdp_mode.run_epubcheck_for_edition(self.edition)
        report = json.loads(self.path.with_name("ebook.epub.epubcheck.json").read_text())
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["stdout"], "partial")

    def test_explicit_artifact_selects_exact_file_and_revalidates_changed_bytes(self):
        (self.root / "BOOK.EPUB3").write_bytes(b"old artifact")
        with patch("subprocess.run", side_effect=self.validator()):
            kdp_mode.run_epubcheck_for_edition(self.edition, artifact_path=self.path)
            self.path.write_bytes(b"new artifact")
            kdp_mode.run_epubcheck_for_edition(self.edition, artifact_path=self.path)
        reports = [json.loads(path.read_text()) for path in (self.root / ".epubcheck_reports").glob("*.json")]
        self.assertEqual(len({report["artifact_sha256"] for report in reports}), 2)

    def test_artifact_from_another_edition_is_rejected(self):
        with patch("subprocess.run") as process:
            with self.assertRaises(ValueError):
                kdp_mode.run_epubcheck_for_edition(self.edition, artifact_path=self.root.parent / "other.epub")
        process.assert_not_called()

    def test_export_metadata_matches_frontmatter_context(self):
        (self.root / "kdp_merged.md").write_text("# Chapter\n\nText.")
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as process:
            kdp_mode.build_epub_for_edition(self.edition)
        command = process.call_args.args[0]
        for name in ("title", "subtitle", "author", "publisher"):
            self.assertIn(f"--metadata={name}:{getattr(self.edition, name)}", command)

    def test_full_build_validates_returned_artifact_before_pdf_and_preserves_old_alias(self):
        old = self.root / "BOOK.EPUB3"
        old.write_bytes(b"previous artifact")
        with patch.object(kdp_mode, "build_frontmatter_files"), patch.object(kdp_mode, "build_merged_kdp_source"), patch.object(kdp_mode, "build_epub_for_edition", return_value=self.path), patch.object(kdp_mode, "run_epubcheck_for_edition", side_effect=RuntimeError("FAIL")) as validator, patch.object(kdp_mode, "build_print_pdf_for_edition") as pdf:
            with self.assertRaises(RuntimeError):
                kdp_mode.gaiden_build_full_book(self.edition)
        validator.assert_called_once_with(self.edition, artifact_path=self.path)
        pdf.assert_not_called()
        self.assertEqual(old.read_bytes(), b"previous artifact")
