from django.test import SimpleTestCase

from gaiden.application.author_studio.extract_core_text import apply_core_text_policy, identify_source_provider
from gaiden.domain.author_studio.enums import SourceProvider


BODY = " ".join(["This is the preserved narrative body with dialogue and meaningful literary content."] * 20)


class CoreTextPolicyTests(SimpleTestCase):
    def test_removes_gutenberg_license_and_preserves_chapter(self):
        text = f"Project Gutenberg metadata\n*** START OF THE PROJECT GUTENBERG EBOOK TEST ***\nTITLE\nCHAPTER 1\n{BODY}\n*** END OF THE PROJECT GUTENBERG EBOOK TEST ***\nLicense"
        result = apply_core_text_policy(text)
        self.assertNotIn("Project Gutenberg metadata", result.text)
        self.assertNotIn("License", result.text)
        self.assertIn("CHAPTER 1", result.text)
        self.assertIn("preserved narrative", result.text)
        self.assertFalse(result.needs_review)

    def test_removes_standard_ebooks_and_internet_archive_metadata(self):
        text = f"TITLE\nStandard Ebooks\nInternet Archive\nISBN 123\nCHAPTER 1\n{BODY}"
        result = apply_core_text_policy(text)
        self.assertNotIn("Standard Ebooks", result.text)
        self.assertNotIn("Internet Archive", result.text)
        self.assertNotIn("ISBN", result.text)

    def test_provider_detection(self):
        self.assertEqual(identify_source_provider("Published by Standard Ebooks"), SourceProvider.STANDARD_EBOOKS)
        self.assertEqual(identify_source_provider("Downloaded from archive.org"), SourceProvider.INTERNET_ARCHIVE)

    def test_removes_images_and_preface_but_keeps_title_and_chapter(self):
        text = f"THE HOUND\n![cover](cover.jpg)\nPREFACE\nEditorial material.\nCHAPTER 1\n{BODY}"
        result = apply_core_text_policy(text)
        self.assertIn("THE HOUND", result.text)
        self.assertNotIn("cover.jpg", result.text)
        self.assertNotIn("PREFACE", result.text)
        self.assertNotIn("Editorial material", result.text)
        self.assertIn("CHAPTER 1", result.text)

    def test_uncertain_text_is_preserved_for_review(self):
        text = "A short literary fragment without an identifiable structure."
        result = apply_core_text_policy(text)
        self.assertEqual(result.text, text)
        self.assertTrue(result.needs_review)
