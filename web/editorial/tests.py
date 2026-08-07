from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from .frontmatter import build_context, language_display, render_frontmatter, render_template
from .models import (
    Contributor,
    ContributorRole,
    Edition,
    Language,
    PipelineArtifact,
    Seal,
    Work,
)


class FrontmatterUtilityTests(SimpleTestCase):
    def test_language_display_supports_portuguese_variants(self):
        self.assertEqual(language_display("ptbr"), "Português")
        self.assertEqual(language_display("pt-br"), "Português")

    def test_language_display_preserves_unknown_code(self):
        self.assertEqual(language_display("fr"), "fr")

    def test_render_template_reports_missing_variable(self):
        rendered = render_template("{title} — {missing}", {"title": "Gaiden"})
        self.assertIn("[MISSING", rendered)
        self.assertIn("missing", rendered)


class EditorialModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.language = Language.objects.create(
            code="en",
            name="English",
            native_name="English",
        )
        cls.seal = Seal.objects.create(slug="mantaquest", name="MantaQuest")
        cls.author = Contributor.objects.create(
            name="Arthur Conan Doyle",
            role=ContributorRole.AUTHOR,
        )
        cls.work = Work.objects.create(
            code="book_0001",
            title="A Study in Scarlet",
            original_language=cls.language,
            author=cls.author,
        )
        cls.edition = Edition.objects.create(
            work=cls.work,
            language=cls.language,
            seal=cls.seal,
            title="A Study in Scarlet",
            author=cls.author.name,
            adapter="RinoBooks Editorial",
            publisher="RinoBooks",
            language_code="en",
        )

    def test_model_string_representations_are_operator_friendly(self):
        self.assertEqual(str(self.language), "English (en)")
        self.assertEqual(str(self.seal), "MantaQuest")
        self.assertIn("Arthur Conan Doyle", str(self.work))
        self.assertIn("A Study in Scarlet", str(self.edition))

    def test_edition_defaults_are_safe_and_deterministic(self):
        self.assertEqual(self.edition.publication_year, 2026)
        self.assertEqual(self.edition.city, "Rio de Janeiro")
        self.assertFalse(self.edition.lock_translate)
        self.assertFalse(self.edition.lock_refine)
        self.assertFalse(self.edition.lock_polish)

    def test_edition_identity_is_unique_per_work_language_and_seal(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Edition.objects.create(
                    work=self.work,
                    language=self.language,
                    seal=self.seal,
                )

    def test_preserved_work_fields_receive_safe_defaults(self):
        self.assertEqual(self.work.subtitle, "")
        self.assertEqual(self.work.enabled_languages, [])
        self.assertEqual(self.work.source_format, "TXT")
        self.assertEqual(self.work.notes, "")

    def test_pipeline_artifact_write_populates_preserved_fields(self):
        artifact = PipelineArtifact.objects.create(
            work_code="book_0001",
            language_code="en",
            stage="raw",
            relpath="book_0001/en/raw.txt",
            filename="raw.txt",
        )
        self.assertEqual(artifact.status, "OK")
        self.assertEqual(artifact.sha256, "")

    def test_frontmatter_context_uses_canonical_metadata(self):
        context = build_context(self.edition)
        self.assertEqual(context["book_code"], "book_0001")
        self.assertEqual(context["title"], "A Study in Scarlet")
        self.assertEqual(context["author"], "Arthur Conan Doyle")
        self.assertEqual(context["imprint"], "RinoBooks")

    def test_frontmatter_renders_required_sections(self):
        rendered = render_frontmatter(self.edition)
        self.assertIn("frontispiece", rendered)
        self.assertIn("copyright", rendered)
        self.assertIn("# Frontispiece", rendered["frontispiece"])
        self.assertIn("A Study in Scarlet", rendered["frontispiece"])
