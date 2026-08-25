from __future__ import annotations

import hashlib
import unittest

from gaiden.application.normalization.block_normalizer import (
    CONTRACT_VERSION,
    NormalizeContractError,
    normalize_extracted_text,
)


class FakeClassifier:
    model = "Qwen/test"

    def __init__(self, mutate=None):
        self.mutate = mutate
        self.calls = 0

    def classify(self, *, source_sha256, blocks):
        self.calls += 1
        decisions = []
        for block in blocks:
            text = str(block["text"])
            lower = text.casefold()
            decision = "KEEP_BODY"
            family = "none"
            if "project gutenberg license" in lower:
                decision, family = "DROP_PLATFORM_LICENSE", "project_gutenberg"
            elif "project gutenberg" in lower:
                decision, family = "DROP_PLATFORM_METADATA", "project_gutenberg"
            elif "digitized by" in lower or "transcribed by" in lower:
                decision = "DROP_DIGITIZATION_CREDIT"
                family = "internet_archive" if "archive" in lower else "other"
            elif "archive.org" in lower:
                decision, family = "DROP_PLATFORM_METADATA", "internet_archive"
            elif "standard ebooks" in lower:
                decision, family = "DROP_EXTERNAL_COLOPHON", "standard_ebooks"
            row = {
                "block_id": block["block_id"],
                "start_offset": block["start_offset"],
                "end_offset": block["end_offset"],
                "decision": decision,
                "source_family": family,
                "confidence": 0.99,
                "evidence": text.strip()[:80] or "blank separator",
            }
            heading = text.strip()
            if heading.upper().startswith("CHAPTER"):
                row.update(
                    decision="KEEP_HEADING",
                    heading_level=1,
                    heading_type="chapter",
                    heading_text=heading,
                )
            elif heading.casefold().startswith("preface"):
                row.update(
                    decision="KEEP_HEADING",
                    heading_level=1,
                    heading_type="preface",
                    heading_text=heading,
                )
            elif heading.casefold().startswith("epilogue"):
                row.update(
                    decision="KEEP_HEADING",
                    heading_level=1,
                    heading_type="epilogue",
                    heading_text=heading,
                )
            decisions.append(row)
        payload = {"schema": CONTRACT_VERSION, "source_sha256": source_sha256, "blocks": decisions}
        if self.mutate:
            self.mutate(payload)
        return payload


class Block01NormalizerTests(unittest.TestCase):
    def normalize(self, text, classifier=None):
        raw_sha = hashlib.sha256(text.encode()).hexdigest()
        return normalize_extracted_text(text, raw_sha256=raw_sha, classifier=classifier or FakeClassifier())

    def test_preserves_authorial_preface_chapters_and_epilogue(self):
        text = "PREFACE\n\nAuthorial words.\n\nCHAPTER I\n\nStory.\n\nEPILOGUE\n\nEnding.\n"
        result = self.normalize(text)
        self.assertEqual(result.normalized_body, text)
        self.assertEqual(
            [row["type"] for row in result.structure_map["structures"]],
            ["preface", "chapter", "epilogue"],
        )

    def test_removes_gutenberg_boilerplate_and_license(self):
        text = "Project Gutenberg ebook metadata\n\nCHAPTER I\n\nBody.\n\nProject Gutenberg license terms"
        result = self.normalize(text)
        self.assertEqual(result.normalized_body, "CHAPTER I\n\nBody.\n\n")
        self.assertEqual(result.manifest["removed_block_count"], 2)

    def test_removes_digitization_credit(self):
        result = self.normalize("Digitized by volunteers.\n\nCHAPTER I\n\nBody.")
        self.assertNotIn("Digitized", result.normalized_body)

    def test_removes_archive_operational_text(self):
        result = self.normalize("Download options at archive.org\n\nCHAPTER I\n\nBody.")
        self.assertNotIn("archive.org", result.normalized_body)

    def test_removes_standard_ebooks_external_colophon(self):
        result = self.normalize("Standard Ebooks production colophon\n\nCHAPTER I\n\nBody.")
        self.assertNotIn("Standard Ebooks", result.normalized_body)

    def test_subchapter_is_preserved(self):
        result = self.normalize("CHAPTER I\n\nSection II\n\nBody.")
        self.assertIn("Section II", result.normalized_body)
        self.assertEqual(result.structure_map["structures"][1]["type"], "subchapter")

    def test_invalid_schema_is_rejected(self):
        def mutate(payload):
            payload["schema"] = "wrong"
        with self.assertRaisesRegex(NormalizeContractError, "Versão"):
            self.normalize("CHAPTER I\n\nBody.", FakeClassifier(mutate))

    def test_unknown_field_is_rejected(self):
        def mutate(payload):
            payload["unexpected"] = True
        with self.assertRaisesRegex(NormalizeContractError, "desconhecidos"):
            self.normalize("CHAPTER I\n\nBody.", FakeClassifier(mutate))

    def test_invalid_offset_is_rejected(self):
        def mutate(payload):
            payload["blocks"][0]["end_offset"] += 1
        with self.assertRaisesRegex(NormalizeContractError, "Offsets"):
            self.normalize("CHAPTER I\n\nBody.", FakeClassifier(mutate))

    def test_missing_evidence_is_rejected(self):
        def mutate(payload):
            payload["blocks"][0]["evidence"] = ""
        with self.assertRaisesRegex(NormalizeContractError, "sem evidência"):
            self.normalize("CHAPTER I\n\nBody.", FakeClassifier(mutate))

    def test_invented_heading_is_rejected(self):
        def mutate(payload):
            payload["blocks"][0]["heading_text"] = "CHAPTER 99"
        with self.assertRaisesRegex(NormalizeContractError, "inventado"):
            self.normalize("CHAPTER I\n\nBody.", FakeClassifier(mutate))

    def test_review_required_is_not_silently_kept(self):
        def mutate(payload):
            payload["blocks"][1]["decision"] = "REVIEW_REQUIRED"
        result = self.normalize("CHAPTER I\n\nAmbiguous material.", FakeClassifier(mutate))
        self.assertTrue(result.manifest["review_required"])
        self.assertNotIn("Ambiguous", result.normalized_body)

    def test_manifest_hash_matches_normalized_bytes(self):
        result = self.normalize("CHAPTER I\n\nBody.")
        digest = hashlib.sha256(result.normalized_body.encode()).hexdigest()
        self.assertEqual(result.manifest["normalized_sha256"], digest)

    def test_structure_offsets_reference_normalized_body(self):
        result = self.normalize("Metadata from Project Gutenberg\n\nCHAPTER I\n\nBody.")
        row = result.structure_map["structures"][0]
        self.assertEqual(
            result.normalized_body[row["start_offset"] : row["end_offset"]],
            "CHAPTER I",
        )

    def test_classifier_cannot_rewrite_body(self):
        text = "CHAPTER I\n\nExact body, byte for byte.\n"
        result = self.normalize(text)
        self.assertEqual(result.normalized_body, text)

    def test_very_long_paragraph_is_segmented_without_data_loss(self):
        text = "CHAPTER I\n\n" + ("long sentence. " * 3_000)
        result = self.normalize(text)
        self.assertEqual(result.normalized_body, text)
        self.assertGreater(result.manifest["kept_block_count"], 2)


if __name__ == "__main__":
    unittest.main()
