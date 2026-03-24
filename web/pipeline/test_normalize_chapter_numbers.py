from pathlib import Path
import sys

from django.test import SimpleTestCase

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from gaiden.normalize import normalize_text_v2


class NormalizeChapterNumbersTests(SimpleTestCase):
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
