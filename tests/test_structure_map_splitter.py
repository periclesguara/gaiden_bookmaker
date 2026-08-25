from __future__ import annotations

import hashlib
import unittest

from gaiden.application.translation.chapter_splitter import (
    STRUCTURE_SPLITTER_VERSION,
    ChapterSplitError,
    split_normalized_body,
)


class NormalizeStructureSplitterTests(unittest.TestCase):
    def structure_map(self, text, headings, *, review_required=False):
        structures = []
        cursor = 0
        for sequence, (heading, kind) in enumerate(headings, start=1):
            start = text.index(heading, cursor)
            cursor = start + len(heading)
            structures.append(
                {
                    "sequence": sequence,
                    "type": kind,
                    "heading_original": heading,
                    "start_offset": start,
                    "end_offset": cursor,
                }
            )
        return {
            "schema": "gaiden_structure_map_v1",
            "normalized_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "validated": not review_required,
            "review_required": review_required,
            "structures": structures,
        }

    def test_reconstructs_normalized_body_exactly(self):
        text = "PREFACE\n\nWords.\n\nCHAPTER I\n\nOne.\n\nCHAPTER II\n\nTwo.\n"
        result = split_normalized_body(
            text.encode(),
            self.structure_map(
                text,
                [("PREFACE", "preface"), ("CHAPTER I", "chapter"), ("CHAPTER II", "chapter")],
            ),
        )
        rebuilt = "".join(text[u.start_offset : u.end_offset] for u in result.units)
        self.assertEqual(rebuilt, text)
        self.assertTrue(result.validated)
        self.assertEqual(result.strategy, "normalize_structure_map")
        self.assertEqual(result.as_manifest()["splitter_version"], STRUCTURE_SPLITTER_VERSION)

    def test_subchapter_remains_inside_chapter_unit(self):
        text = "CHAPTER I\n\nSection A\n\nBody.\n\nCHAPTER II\n\nEnd."
        structure = self.structure_map(
            text,
            [("CHAPTER I", "chapter"), ("Section A", "subchapter"), ("CHAPTER II", "chapter")],
        )
        result = split_normalized_body(text.encode(), structure)
        self.assertEqual(sum(unit.unit_type == "chapter" for unit in result.units), 2)
        first = result.units[0]
        self.assertIn("Section A", text[first.start_offset : first.end_offset])

    def test_hash_mismatch_is_rejected(self):
        text = "CHAPTER I\n\nBody."
        structure = self.structure_map(text, [("CHAPTER I", "chapter")])
        structure["normalized_sha256"] = "0" * 64
        with self.assertRaisesRegex(ChapterSplitError, "SHA-256"):
            split_normalized_body(text.encode(), structure)

    def test_review_required_blocks_split(self):
        text = "CHAPTER I\n\nBody."
        result = split_normalized_body(
            text.encode(),
            self.structure_map(text, [("CHAPTER I", "chapter")], review_required=True),
        )
        self.assertFalse(result.validated)
        self.assertTrue(result.review_required)
        self.assertEqual(result.units, ())

    def test_invented_heading_is_rejected(self):
        text = "CHAPTER I\n\nBody."
        structure = self.structure_map(text, [("CHAPTER I", "chapter")])
        structure["structures"][0]["heading_original"] = "CHAPTER 99"
        with self.assertRaisesRegex(ChapterSplitError, "Offsets ou heading"):
            split_normalized_body(text.encode(), structure)

    def test_map_without_chapter_requires_review(self):
        text = "PREFACE\n\nOnly front matter."
        result = split_normalized_body(
            text.encode(),
            self.structure_map(text, [("PREFACE", "preface")]),
        )
        self.assertTrue(result.review_required)
        self.assertEqual(result.units, ())


if __name__ == "__main__":
    unittest.main()
