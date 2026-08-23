import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from gaiden.normalize import QwenNormalizeClient, normalize_text_with_qwen, run_cli


class FakeCleanupClient:
    model = "Qwen/test-normalizer"

    def __init__(self, payload, *, fenced=False):
        raw = json.dumps(payload)
        self.response = f"```json\n{raw}\n```" if fenced else raw
        self.calls = []

    def generate(self, *, system, user, max_tokens):
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        return self.response


def cleanup_payload(*ranges, source_kinds=None, confidence=0.99):
    return {
        "schema_version": "normalize_cleanup_v1",
        "confidence": confidence,
        "source_kinds": source_kinds or [],
        "remove_ranges": list(ranges),
        "notes": "",
    }


class QwenNormalizeTests(TestCase):
    def test_removes_only_validated_source_wrappers_and_preserves_body(self):
        lines = [
            "Standard Ebooks — standardebooks.org",
            "This ebook is distributed under the CC0 license.",
            "THE TEST BOOK",
            "By Example Author",
            "CHAPTER I",
            "The first sentence of the actual story.",
            "The second sentence remains exactly as written.",
            "Digitized by the Internet Archive.",
            "Terms of use: archive.org/details/test",
        ]
        client = FakeCleanupClient(
            cleanup_payload(
                {"start_line": 1, "end_line": 2, "reason": "Standard Ebooks wrapper"},
                {"start_line": 8, "end_line": 9, "reason": "Internet Archive wrapper"},
                source_kinds=["standard_ebooks", "internet_archive"],
            ),
            fenced=True,
        )

        normalized, audit = normalize_text_with_qwen(
            "\n".join(lines), client=client, boundary_lines=4, max_removed_ratio=0.75
        )

        self.assertNotIn("Standard Ebooks", normalized)
        self.assertNotIn("archive.org", normalized)
        self.assertIn("THE TEST BOOK\nBy Example Author\nCHAPTER I", normalized)
        self.assertIn("The second sentence remains exactly as written.", normalized)
        self.assertEqual(audit["removed_line_count"], 4)
        self.assertEqual(audit["qwen_model"], client.model)
        self.assertEqual(audit["source_kinds"], ["internet_archive", "standard_ebooks"])
        self.assertEqual(len(client.calls), 1)

    def test_gutenberg_markers_are_removed_before_qwen_boundary_review(self):
        raw = "\n".join(
            [
                "Project Gutenberg metadata",
                "*** START OF THE PROJECT GUTENBERG EBOOK TEST ***",
                "THE BOOK",
                "CHAPTER I",
                "The story remains.",
                "*** END OF THE PROJECT GUTENBERG EBOOK TEST ***",
                "Project Gutenberg full license",
            ]
        )
        client = FakeCleanupClient(cleanup_payload(source_kinds=[]))

        normalized, audit = normalize_text_with_qwen(raw, client=client, boundary_lines=3)

        self.assertEqual(normalized, "THE BOOK\nCHAPTER I\nThe story remains.")
        self.assertIn("project_gutenberg", audit["source_kinds"])
        self.assertTrue(audit["qwen_used"])

    def test_rejects_deletion_outside_inspected_boundaries(self):
        lines = [f"Body line {number}" for number in range(1, 21)]
        lines[9] = "Project Gutenberg license inside the manuscript"
        client = FakeCleanupClient(
            cleanup_payload(
                {"start_line": 10, "end_line": 10, "reason": "claimed source wrapper"},
                source_kinds=["project_gutenberg"],
            )
        )

        with self.assertRaisesRegex(ValueError, "only inspected boundary lines"):
            normalize_text_with_qwen("\n".join(lines), client=client, boundary_lines=2)

    def test_rejects_range_without_source_evidence(self):
        client = FakeCleanupClient(
            cleanup_payload(
                {"start_line": 1, "end_line": 2, "reason": "model guessed"},
            )
        )

        with self.assertRaisesRegex(ValueError, "no source-boilerplate evidence"):
            normalize_text_with_qwen(
                "TITLE\nBy the Author\nCHAPTER I\nThe story remains.",
                client=client,
                boundary_lines=2,
            )

    def test_rejects_excessive_deletion_even_at_high_confidence(self):
        raw = "\n".join(
            ["Standard Ebooks license text"] * 8
            + ["CHAPTER I", "The story remains."]
        )
        client = FakeCleanupClient(
            cleanup_payload(
                {"start_line": 1, "end_line": 8, "reason": "source wrapper"},
                source_kinds=["standard_ebooks"],
            )
        )

        with self.assertRaisesRegex(ValueError, "exceeds the safety ratio"):
            normalize_text_with_qwen(
                raw,
                client=client,
                boundary_lines=10,
                max_removed_ratio=0.35,
            )

    def test_non_loopback_endpoint_rejects_placeholder_key(self):
        with self.assertRaisesRegex(ValueError, "real API key"):
            QwenNormalizeClient(
                base_url="https://models.example.invalid/v1",
                api_key="placeholder",
            )

    def test_non_loopback_endpoint_requires_https(self):
        with self.assertRaisesRegex(ValueError, "must use https"):
            QwenNormalizeClient(
                base_url="http://models.example.invalid/v1",
                api_key="real-test-key",
            )

    def test_cli_writes_matrix_compatible_artifacts(self):
        client = FakeCleanupClient(
            cleanup_payload(
                {"start_line": 1, "end_line": 1, "reason": "archive.org wrapper"},
                source_kinds=["internet_archive"],
            )
        )
        with TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            raw_dir = data_dir / "raw" / "book_0042" / "en"
            raw_dir.mkdir(parents=True)
            body_lines = ["THE BOOK"] + [
                f"The story remains in paragraph {number}." for number in range(1, 11)
            ]
            (raw_dir / "source.txt").write_text(
                "archive.org terms of use\n" + "\n".join(body_lines),
                encoding="utf-8",
            )

            exit_code = run_cli(["book_0042", "en"], client=client, data_dir=data_dir)

            output_dir = data_dir / "normalized" / "book_0042" / "en"
            normalized_path = output_dir / "book_0042_en_v2.txt"
            report = json.loads((output_dir / "normalize_report.json").read_text())
            self.assertEqual(exit_code, 0)
            self.assertEqual(normalized_path.read_text(), "\n".join(body_lines))
            self.assertEqual(report["status"], "OK")
            self.assertEqual(report["removed_line_count"], 1)
            self.assertEqual(report["normalized_path"], str(normalized_path))
