from django.db import IntegrityError, transaction
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .frontmatter import (
    build_context,
    build_frontmatter_files,
    build_merged_frontmatter,
    language_display,
    render_frontmatter,
    render_template,
)
from .provenance_forms import SourceProvenanceForm
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
            about_edition_template="A verified editorial edition.",
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
        self.assertEqual(self.work.source_provenance, {})

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
        self.assertIn("title_page", rendered)
        self.assertIn("copyright", rendered)
        self.assertIn("# Title Page", rendered["title_page"])
        self.assertIn("A Study in Scarlet", rendered["title_page"])

    def test_source_record_uses_provenance_without_overwriting_editorial_fields(self):
        self.work.source_provenance = {
            "original_title": "A Study in Scarlet — source title",
            "source_author": "Doyle, Arthur Conan",
            "original_publication_year": 1887,
            "original_publication_basis": "copyright_notice",
            "source_credits": "Distributed Proofreading Team",
            "source_filename": "source.epub",
            "source_sha256": "a" * 64,
        }
        self.work.save(update_fields=["source_provenance"])

        rendered = render_frontmatter(self.edition)

        self.assertIn("source_record", rendered)
        self.assertIn("# Original Source Record", rendered["source_record"])
        self.assertIn("Source credits", rendered["source_record"])
        self.assertEqual(self.edition.title, "A Study in Scarlet")
        self.assertEqual(self.edition.author, "Arthur Conan Doyle")

    def test_frontmatter_writes_identical_legacy_alias_in_canonical_order(self):
        self.work.source_provenance = {
            "original_title": "A Study in Scarlet",
            "source_author": "Arthur Conan Doyle",
            "source_filename": "source.txt",
            "source_sha256": "b" * 64,
        }
        self.work.save(update_fields=["source_provenance"])

        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            build_frontmatter_files(self.edition, base)
            output = base / self.work.code / self.language.code
            title_page = (output / "title_page.md").read_bytes()
            legacy_alias = (output / "frontispiece.md").read_bytes()

            self.assertEqual(title_page, legacy_alias)
            self.assertTrue((output / "source_record.md").exists())

        merged = build_merged_frontmatter(self.edition)
        self.assertLess(merged.index("# Title Page"), merged.index("# Copyright"))
        self.assertLess(merged.index("# Copyright"), merged.index("# Original Source Record"))
        self.assertLess(merged.index("# Original Source Record"), merged.index("# About this Edition"))

    def test_manual_provenance_keeps_filename_and_hash_read_only(self):
        self.work.source_provenance = {
            "original_title": "Old title",
            "source_filename": "original.epub",
            "source_sha256": "c" * 64,
        }
        self.work.save(update_fields=["source_provenance"])
        form = SourceProvenanceForm(
            {
                "original_title": "Reviewed title",
                "source_filename": "forged.epub",
                "source_sha256": "0" * 64,
            },
            work=self.work,
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.work.refresh_from_db()
        self.assertEqual(self.work.source_provenance["original_title"], "Reviewed title")
        self.assertEqual(self.work.source_provenance["source_filename"], "original.epub")
        self.assertEqual(self.work.source_provenance["source_sha256"], "c" * 64)

    def test_manual_ui_uses_title_page_and_read_only_source_identity(self):
        self.work.source_provenance = {
            "original_title": "A Study in Scarlet",
            "source_filename": "original.epub",
            "source_sha256": "e" * 64,
        }
        self.work.save(update_fields=["source_provenance"])

        response = self.client.get(reverse("edition_edit", args=[self.edition.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Title Page")
        self.assertContains(response, "Registro da fonte original")
        self.assertContains(response, 'name="source_filename"', html=False)
        self.assertContains(response, 'name="source_sha256"', html=False)
        self.assertContains(response, "disabled", count=2)
        self.assertNotContains(response, "Frontispicio")
