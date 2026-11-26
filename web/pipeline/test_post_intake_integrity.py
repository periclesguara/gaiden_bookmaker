import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from pipeline.services import md_quality, paths


class PostIntakeIntegrityTests(SimpleTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings_override = override_settings(BASE_DIR=Path(self.directory.name) / "web")
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.edition = SimpleNamespace(
            id=42, work=SimpleNamespace(code="fixture"),
            language=SimpleNamespace(code="en"),
        )
        paths.edition_build_dir(self.edition).mkdir(parents=True)

    def test_analysis_preserves_literary_elements_and_source_references_byte_for_byte(self):
        manuscripts = (
            '# Chapter I\r\n\r\n"All rights reserved," she read.\r\n',
            '# Capítulo 1\n\n> Carta: li a edição Penguin Classics.\n\n— Ana\n',
            '# Chapitre I\n\n*Journal*\n\nProject Gutenberg est une source.\n',
            '# Sources\n\n| Source | Note |\n| --- | --- |\n| Project Gutenberg | [^1] |\n\n[^1]: Printed in the United States.\n',
            '# Chapter I\n\nContents of the letter\n\nA secret... 12\n\nThe letter ends.\n',
            '# Poem\n\nOne line  \nAnother line\n\n***\n\nA new scene.\n',
        )
        for index, manuscript in enumerate(manuscripts):
            with self.subTest(index=index):
                self.edition.work.code = f"fixture_{index}"
                source = paths.pre_qa_md_path(self.edition)
                source.parent.mkdir(parents=True)
                source.write_bytes(manuscript.encode())
                result = md_quality.run_quality_analysis(self.edition)
                self.assertEqual(Path(result["path"]).read_bytes(), source.read_bytes())
                self.assertEqual(result["status"], "REVIEW_REQUIRED")

    def test_repeated_analysis_does_not_replace_operator_candidate(self):
        paths.pre_qa_md_path(self.edition).write_text("Original text.")
        paths.qa_md_path(self.edition).write_text("Reviewed text.")
        with self.assertRaises(FileExistsError):
            md_quality.run_quality_analysis(self.edition)
        self.assertEqual(paths.qa_md_path(self.edition).read_text(), "Reviewed text.")

    def test_approval_does_not_overwrite_a_different_approved_manuscript(self):
        paths.pre_qa_md_path(self.edition).write_text("New candidate.")
        final = paths.final_md_path(self.edition)
        final.write_text("Approved text.")
        with self.assertRaises(FileExistsError):
            md_quality.approve_md_final(self.edition)
        self.assertEqual(final.read_text(), "Approved text.")

    def test_empty_candidate_cannot_be_approved(self):
        paths.qa_md_path(self.edition).write_text(" \n\t")
        with self.assertRaises(ValueError):
            md_quality.approve_md_final(self.edition)
        self.assertFalse(paths.final_md_path(self.edition).exists())

    def test_approval_preserves_exact_bytes_and_is_idempotent(self):
        payload = b"# Chapter I\r\n\r\nA letter.  \r\nSigned.\r\n"
        paths.qa_md_path(self.edition).write_bytes(payload)
        first = md_quality.approve_md_final(self.edition)
        final = paths.final_md_path(self.edition)
        timestamp = final.stat().st_mtime_ns
        second = md_quality.approve_md_final(self.edition)
        self.assertEqual(final.read_bytes(), payload)
        self.assertEqual(final.stat().st_mtime_ns, timestamp)
        self.assertEqual(first["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(first, second)
        self.assertEqual(Path(first["snapshot"]).read_bytes(), payload)

    def test_competing_approvals_never_replace_the_winner(self):
        from gaiden.manuscript_artifacts import preserve_manuscript

        final = paths.final_md_path(self.edition)
        def publish(payload):
            try:
                preserve_manuscript(final, payload)
                return payload
            except FileExistsError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(publish, (b"First.", b"Second.")))
        winners = [outcome for outcome in outcomes if outcome is not None]
        self.assertEqual(winners, [final.read_bytes()])

    def test_invalid_utf8_cannot_be_promoted(self):
        paths.qa_md_path(self.edition).write_bytes(b"\xff")
        with self.assertRaises(UnicodeDecodeError):
            md_quality.approve_md_final(self.edition)
        self.assertFalse(paths.final_md_path(self.edition).exists())

    def test_more_images_do_not_override_a_reviewed_candidate(self):
        paths.qa_md_path(self.edition).write_text("Reviewed text.")
        paths.pre_edition_md_path(self.edition).write_text("Old text.\n\n![Scene](01.jpg)")
        md_quality.approve_md_final(self.edition)
        self.assertEqual(paths.final_md_path(self.edition).read_text(), "Reviewed text.")

    def test_analysis_is_idempotent(self):
        paths.pre_qa_md_path(self.edition).write_text("A valid paragraph.\n")
        first = md_quality.run_quality_analysis(self.edition)
        timestamp = paths.qa_md_path(self.edition).stat().st_mtime_ns
        self.assertEqual(first, md_quality.run_quality_analysis(self.edition))
        self.assertEqual(paths.qa_md_path(self.edition).stat().st_mtime_ns, timestamp)
