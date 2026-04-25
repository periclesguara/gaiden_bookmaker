from pathlib import Path
import sys

from django.test import SimpleTestCase

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from gaiden.normalize import normalize_text_v2, roman_to_int


class NormalizeChapterNumbersTests(SimpleTestCase):
    def test_roman_to_int_accepts_only_canonical_roman_numerals(self):
        self.assertEqual(roman_to_int("I"), 1)
        self.assertEqual(roman_to_int("IV"), 4)
        self.assertEqual(roman_to_int("IX"), 9)
        self.assertEqual(roman_to_int("XLII"), 42)
        self.assertEqual(roman_to_int("MCMXCIX"), 1999)
        self.assertIsNone(roman_to_int(""))
        self.assertIsNone(roman_to_int("IIII"))
        self.assertIsNone(roman_to_int("IC"))
        self.assertIsNone(roman_to_int("VX"))
        self.assertIsNone(roman_to_int("IIV"))

    def test_normalize_converts_explicit_roman_chapter_headings_to_arabic(self):
        raw = (
            "Chapter VII—The Trapping of Birdy Edwards\n\n"
            "Body.\n\n"
            "CHAPTER IV\n\n"
            "More body.\n"
        )

        normalized = normalize_text_v2(raw)

        self.assertIn("CHAPTER 7—The Trapping of Birdy Edwards", normalized)
        self.assertIn("CHAPTER 4", normalized)
        self.assertNotIn("Chapter VII", normalized)
        self.assertNotIn("CHAPTER IV", normalized)

    def test_normalize_promoted_standalone_markers_use_arabic_numbers(self):
        raw = (
            "I speak now because no one listened.\n\n"
            "II.\n\n"
            "The second chapter starts here.\n\n"
            "III.\n\n"
            "The third chapter starts here.\n"
        )

        normalized = normalize_text_v2(raw)

        self.assertIn("CHAPTER 1", normalized)
        self.assertIn("CHAPTER 2", normalized)
        self.assertIn("CHAPTER 3", normalized)
        self.assertNotIn("CHAPTER I", normalized)
        self.assertNotIn("CHAPTER II", normalized)
        self.assertNotIn("CHAPTER III", normalized)

    def test_normalize_converts_part_headings_and_preserves_chapter_restart(self):
        raw = (
            "Part I\n\n"
            "Chapter I\n\n"
            "The Warning\n\n"
            "Body.\n\n"
            "Chapter II\n\n"
            "More body.\n\n"
            "Part II\n\n"
            "Chapter I\n\n"
            "The Man\n\n"
            "More body.\n"
        )

        normalized = normalize_text_v2(raw)

        self.assertIn("PART 1", normalized)
        self.assertIn("PART 2", normalized)
        self.assertIn("CHAPTER 1", normalized)
        self.assertIn("CHAPTER 2", normalized)
        self.assertNotIn("Part I", normalized)
        self.assertNotIn("Part II", normalized)
        self.assertNotIn("Chapter I", normalized)

    def test_normalize_inserts_part_markers_when_chapters_restart_without_part_heading(self):
        raw = (
            "Chapter I\n\n"
            "The Warning\n\n"
            "Body.\n\n"
            "Chapter II\n\n"
            "More body.\n\n"
            "Chapter I\n\n"
            "The Man\n\n"
            "More body.\n"
        )

        normalized = normalize_text_v2(raw)

        self.assertIn("PART 1\n\nCHAPTER 1", normalized)
        self.assertIn("PART 2\n\nCHAPTER 1", normalized)

    def test_normalize_removes_isolated_numeric_residue_before_chunk(self):
        raw = (
            "Chapter I\n\n"
            "The Warning\n\n"
            "1\n\n"
            "Body paragraph.\n\n"
            "23\n\n"
            "More body.\n\n"
            "IX\n\n"
            "Final body.\n"
        )

        normalized = normalize_text_v2(raw)

        self.assertIn("CHAPTER 1", normalized)
        self.assertNotIn("\n1\n", normalized)
        self.assertNotIn("\n23\n", normalized)
        self.assertNotIn("\nIX\n", normalized)
        self.assertIn("Body paragraph.", normalized)
        self.assertIn("Final body.", normalized)

    def test_normalize_converts_roman_prefix_headings_to_arabic(self):
        raw = (
            "I. The First Case\n\n"
            "Body.\n\n"
            "## II: The Second Case\n\n"
            "More body.\n\n"
            "III—The Third Case\n\n"
            "Final body.\n"
        )

        normalized = normalize_text_v2(raw)

        self.assertIn("1. The First Case", normalized)
        self.assertIn("## 2: The Second Case", normalized)
        self.assertIn("3—The Third Case", normalized)
        self.assertNotIn("I. The First Case", normalized)
        self.assertNotIn("II: The Second Case", normalized)
        self.assertNotIn("III—The Third Case", normalized)

    def test_normalize_does_not_convert_invalid_roman_like_headings(self):
        raw = (
            "Chapter IC\n\n"
            "Body.\n\n"
            "IIII. Invalid Heading\n\n"
            "More body.\n"
        )

        normalized = normalize_text_v2(raw)

        self.assertIn("Chapter IC", normalized)
        self.assertIn("IIII. Invalid Heading", normalized)
        self.assertNotIn("CHAPTER 99", normalized)
        self.assertNotIn("4. Invalid Heading", normalized)
