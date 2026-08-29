from django.test import SimpleTestCase

from pipeline.services.heading_cleaner import _clean_normalized_text


class HeadingCleanerTests(SimpleTestCase):
    def test_preserves_restarted_chapter_number_after_prose(self):
        cleaned, stats = _clean_normalized_text(
            "PART 1\n\nCHAPTER 1\n\nFirst chapter prose.\n\nPART 2\n\nCHAPTER 1\n\nSecond chapter prose.\n"
        )

        self.assertEqual(cleaned.count("CHAPTER 1"), 2)
        self.assertEqual(stats["deduped_headings"], 0)

    def test_removes_only_adjacent_duplicate_heading(self):
        cleaned, stats = _clean_normalized_text(
            "CHAPTER 1\n\nCHAPTER 1\n\nChapter prose continues here.\n"
        )

        self.assertEqual(cleaned.count("CHAPTER 1"), 1)
        self.assertEqual(stats["deduped_headings"], 1)
