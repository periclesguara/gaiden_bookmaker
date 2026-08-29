from __future__ import annotations

import unittest
from dataclasses import replace

from gaiden.application.translation.chapter_splitter import (
    ChapterSplitError,
    split_heading_clean,
    validate_coverage,
)


class ChapterTranslationSplitterTests(unittest.TestCase):
    def split(self, text: str, **kwargs):
        return split_heading_clean(text.encode("utf-8"), **kwargs)

    def test_split_recognizes_roman_chapters(self):
        result = self.split("CHAPTER I\n\nFirst.\n\nCHAPTER II\n\nSecond.\n")
        self.assertTrue(result.validated)
        self.assertEqual([unit.chapter_number for unit in result.units], ["I", "II"])

    def test_split_recognizes_arabic_and_word_chapters(self):
        result = self.split("Chapter 1\n\nFirst.\n\nChapter Two\n\nSecond.\n")
        self.assertEqual([unit.chapter_number for unit in result.units], ["1", "2"])

    def test_split_recognizes_trailing_period_without_title(self):
        result = self.split("CHAPTER I.\n\nFirst.\n\nCHAPTER II.\n\nSecond.\n")
        self.assertTrue(result.validated)
        self.assertEqual([unit.chapter_number for unit in result.units], ["I", "II"])

    def test_split_recognizes_standalone_markdown_roman_chapters(self):
        result = self.split("# Novel\n\n## I\n\nFirst.\n\n## II.\n\nSecond.\n")
        self.assertTrue(result.validated)
        chapters = [unit for unit in result.units if unit.unit_type == "chapter"]
        self.assertEqual([unit.chapter_number for unit in chapters], ["I", "II"])
        self.assertEqual([unit.heading for unit in chapters], ["I", "II."])

    def test_split_rejects_noncanonical_roman_markdown_heading(self):
        result = self.split("## DID\n\nThis is a section title, not a chapter.\n")
        self.assertFalse(result.validated)
        self.assertEqual(result.units, ())

    def test_split_recognizes_heading_kinds_accepted_upstream(self):
        for heading in ("ADVENTURE I.", "SECTION 1", "CAPÍTULO 1", "KAPITEL 1", "CHAPITRE 1"):
            with self.subTest(heading=heading):
                result = self.split(f"{heading}\n\nBody.\n")
                self.assertTrue(result.validated)
                self.assertEqual([unit.unit_type for unit in result.units], ["chapter"])

    def test_split_recognizes_book_part_and_chapter_combinations(self):
        result = self.split("BOOK I\n\nOpening.\n\nPART 1\n\nBridge.\n\nCHAPTER I\n\nBody.\n")
        self.assertEqual(len(result.units), 3)
        self.assertEqual(result.units[-1].unit_type, "chapter")
        self.assertEqual(result.units[-1].part_number, 1)

    def test_content_before_first_chapter_is_preserved_as_preliminaries(self):
        text = "Title\nDedication\n\nCHAPTER I\n\nBody.\n"
        result = self.split(text)
        self.assertEqual(result.units[0].unit_id, "0000")
        self.assertEqual(result.units[0].unit_type, "preliminaries")
        self.assertEqual(text[: result.units[0].end_offset], "Title\nDedication\n\n")

    def test_epilogue_and_appendix_are_individual_units(self):
        result = self.split("CHAPTER 1\n\nBody.\n\nEPILOGUE\n\nEnd.\n\nAPPENDIX A\n\nNotes.\n")
        self.assertEqual([unit.unit_type for unit in result.units], ["chapter", "epilogue", "appendix"])

    def test_duplicate_toc_headings_are_not_units(self):
        text = (
            "CONTENTS\nCHAPTER I\nCHAPTER II\n\n"
            "CHAPTER I\n\nActual first chapter body.\n\n"
            "CHAPTER II\n\nActual second chapter body.\n"
        )
        result = self.split(text)
        chapter_units = [unit for unit in result.units if unit.unit_type == "chapter"]
        self.assertEqual(len(chapter_units), 2)
        self.assertIn("CONTENTS\nCHAPTER I\nCHAPTER II", text[: chapter_units[0].start_offset])

    def test_chapter_word_inside_paragraph_is_not_a_heading(self):
        result = self.split("CHAPTER I\n\nShe remembered chapter two from school.\n")
        self.assertEqual(len(result.units), 1)

    def test_common_special_word_inside_prose_is_not_a_heading(self):
        result = self.split(
            "CHAPTER 1\n\n"
            "It was called an\n"
            "introduction\n"
            "—of a most unwelcome sort.\n\n"
            "CHAPTER 2\n\n"
            "The story continues.\n"
        )

        self.assertTrue(result.validated)
        self.assertEqual([unit.unit_type for unit in result.units], ["chapter", "chapter"])
        self.assertNotIn("introduction", [unit.heading.casefold() for unit in result.units])

    def test_delimited_title_case_special_heading_is_preserved(self):
        result = self.split(
            "Introduction\n\n"
            "Opening material.\n\n"
            "CHAPTER 1\n\n"
            "The story starts.\n"
        )

        self.assertTrue(result.validated)
        self.assertEqual([unit.unit_type for unit in result.units], ["introduction", "chapter"])

    def test_file_without_reliable_heading_requires_review(self):
        result = self.split("A continuous story without structural headings.\n")
        self.assertFalse(result.validated)
        self.assertTrue(result.review_required)
        self.assertEqual(result.units, ())

    def test_structural_units_without_a_chapter_require_review(self):
        for heading in ("PREFACE", "BOOK I", "PART 1"):
            with self.subTest(heading=heading):
                result = self.split(f"{heading}\n\nOpening material only.\n")
                self.assertFalse(result.validated)
                self.assertTrue(result.review_required)
                self.assertEqual(sum(unit.unit_type == "chapter" for unit in result.units), 0)
                self.assertIn("Nenhum capítulo estrutural confiável foi encontrado.", result.warnings)

    def test_discontinuous_chapter_numbers_require_review(self):
        result = self.split("CHAPTER 1\n\nFirst.\n\nCHAPTER 3\n\nThird.\n")
        self.assertFalse(result.validated)
        self.assertTrue(result.review_required)
        self.assertIn("descontínua", result.warnings[0])

    def test_chapter_sequence_that_does_not_start_at_one_requires_review(self):
        result = self.split("## II\n\nSecond.\n\n## III\n\nThird.\n")
        self.assertFalse(result.validated)
        self.assertTrue(result.review_required)
        self.assertIn("não começa em 1", result.warnings[0])

    def test_qwen_can_suggest_replacement_for_discontinuous_deterministic_split(self):
        text = "CHAPTER 1\n\nFirst.\n\nCHAPTER 3\n\nThird.\n"
        result = self.split(
            text,
            qwen_detector=lambda value: {
                "schema": "gaiden_chapter_detection_v1",
                "units": [
                    {
                        "sequence": 1,
                        "unit_type": "chapter",
                        "heading": "CHAPTER 1",
                        "start_offset": 0,
                        "end_offset": len(value),
                        "confidence": 0.91,
                    }
                ],
            },
        )
        self.assertTrue(result.validated)
        self.assertEqual(result.strategy, "qwen_supervised")

    def test_valid_qwen_fallback_is_supervised_and_preserves_text(self):
        text = "Opening section.\nSecond section.\n"

        def detector(value):
            boundary = value.index("Second")
            return {
                "schema": "gaiden_chapter_detection_v1",
                "units": [
                    {
                        "sequence": 1,
                        "unit_type": "introduction",
                        "heading": "",
                        "start_offset": 0,
                        "end_offset": boundary,
                        "confidence": 0.95,
                    },
                    {
                        "sequence": 2,
                        "unit_type": "chapter",
                        "heading": "Second section.",
                        "start_offset": boundary,
                        "end_offset": len(value),
                        "confidence": 0.95,
                    },
                ],
            }

        result = self.split(text, qwen_detector=detector)
        self.assertEqual(result.strategy, "qwen_supervised")
        self.assertTrue(result.validated)

    def test_qwen_fallback_rejects_invalid_offsets(self):
        with self.assertRaisesRegex(ChapterSplitError, "lacuna"):
            self.split(
                "No headings here.\n",
                qwen_detector=lambda value: {
                    "schema": "gaiden_chapter_detection_v1",
                    "units": [
                        {
                            "unit_type": "chapter",
                            "heading": "",
                            "start_offset": 1,
                            "end_offset": len(value),
                            "confidence": 0.99,
                        }
                    ],
                },
            )

    def test_coverage_rejects_gap(self):
        result = self.split("CHAPTER I\n\nOne.\n\nCHAPTER II\n\nTwo.\n")
        units = list(result.units)
        units[1] = replace(units[1], start_offset=units[1].start_offset + 1)
        with self.assertRaisesRegex(ChapterSplitError, "lacuna"):
            validate_coverage("CHAPTER I\n\nOne.\n\nCHAPTER II\n\nTwo.\n", units)

    def test_coverage_rejects_overlap(self):
        result = self.split("CHAPTER I\n\nOne.\n\nCHAPTER II\n\nTwo.\n")
        units = list(result.units)
        units[1] = replace(units[1], start_offset=units[1].start_offset - 1)
        with self.assertRaisesRegex(ChapterSplitError, "sobreposição"):
            validate_coverage("CHAPTER I\n\nOne.\n\nCHAPTER II\n\nTwo.\n", units)

    def test_units_reconstruct_source_exactly(self):
        text = "Title  \n\nCHAPTER I\n\nText, punctuation!\n\nCHAPTER II\n\nLast.\n"
        result = self.split(text)
        rebuilt = "".join(text[unit.start_offset : unit.end_offset] for unit in result.units)
        self.assertEqual(rebuilt.encode("utf-8"), text.encode("utf-8"))

    def test_oversized_chapter_splits_only_between_paragraphs(self):
        text = "CHAPTER I\n\n" + ("First sentence. " * 3) + "\n\n" + ("Second sentence. " * 3) + "\n"
        result = self.split(text, alert_characters=40, hard_limit_characters=70)
        self.assertTrue(result.validated)
        self.assertGreater(len(result.units), 1)
        self.assertTrue(all(unit.unit_type == "oversized_chapter_part" for unit in result.units))
        rebuilt = "".join(text[unit.start_offset : unit.end_offset] for unit in result.units)
        self.assertEqual(rebuilt, text)

    def test_oversized_single_paragraph_requires_review_instead_of_cutting_sentence(self):
        text = "CHAPTER I\n" + ("A long sentence remains intact. " * 10)
        result = self.split(text, alert_characters=40, hard_limit_characters=70)
        self.assertTrue(result.review_required)
        self.assertEqual(len(result.units), 1)


if __name__ == "__main__":
    unittest.main()
