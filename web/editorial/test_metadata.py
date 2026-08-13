from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from editorial.models import (
    Contributor,
    ContributorRole,
    Edition,
    EditionMetadata,
    Language,
    Seal,
    Work,
)
from editorial.services.metadata import validate_metadata


class EditionMetadataTests(TestCase):
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
            code="book_0041",
            title="A Study in Scarlet",
            original_language=cls.language,
            author=cls.author,
            year=1887,
        )
        cls.edition = Edition.objects.create(
            work=cls.work,
            language=cls.language,
            seal=cls.seal,
            title="A Study in Scarlet",
            author=cls.author.name,
            publication_year=2026,
            language_code="en",
        )

    def _payload(self, **overrides):
        payload = {
            "edition_code": "BOOK_0041-ENUS-EPUB-01",
            "commercial_title": "A Study in Scarlet",
            "subtitle": "Modern English Edition",
            "original_title": "A Study in Scarlet",
            "author_first_name": "Arthur",
            "author_last_name": "Conan Doyle",
            "regional_language": "en-US",
            "original_language": "en",
            "imprint_name": "RinoBooks",
            "collection_name": "Sherlock Holmes",
            "edition_number": "1",
            "publication_year": "2026",
            "edition_format": "EPUB",
            "slug": "A Study in Scarlet -- Modern English!",
            "seo_title": "A Study in Scarlet: Modern English | RinoBooks",
            "seo_description": (
                "Discover Sherlock Holmes in a clear modern English edition, "
                "with careful editorial context for new and returning readers."
            ),
            "description": "A complete commercial description of this edition.",
            "short_description": "Sherlock Holmes in clear modern English.",
            "keywords": "Sherlock Holmes\nDetective fiction",
            "primary_category": "Fiction",
            "subcategory": "Detective Fiction",
            "theme": "Deduction",
            "target_audience": "Classic mystery readers",
            "cover_alt": "Cover of A Study in Scarlet by Arthur Conan Doyle",
            "work_type": "PUBLIC_DOMAIN",
            "base_work_year": "1887",
            "consulted_source": "Verified first-edition transcription.",
            "legal_basis": "Public-domain source.",
            "edition_nature": "Modern English editorial edition",
            "editorial_modifications": "Spelling modernization and editorial notes.",
            "authorized_territories": "Worldwide where the source is public domain.",
            "blocked_territories": "Territories where rights verification is pending.",
            "rights_evidence": "Rights worksheet reviewed by an editor.",
            "price": "19.90",
            "currency": "BRL",
            "expected_release_date": "2026-09-01",
            "hotmart_url": "",
            "lulu_url": "",
            "sample_title": "Opening chapter",
            "sample_content": "In the year 1878 I took my degree...",
            "promotional_images": "https://assets.example.invalid/study-card.jpg",
        }
        payload.update(overrides)
        return payload

    def test_page_creates_and_edits_canonical_metadata(self):
        url = reverse("edition_metadata_edit", args=[self.edition.id])
        response = self.client.post(url, {**self._payload(), "action": "save_draft"})
        self.assertRedirects(response, url)

        metadata = EditionMetadata.objects.get(edition=self.edition)
        self.assertEqual(metadata.slug, "a-study-in-scarlet-modern-english")
        self.assertEqual(metadata.keywords, ["Sherlock Holmes", "Detective fiction"])
        self.assertEqual(metadata.price, Decimal("19.90"))
        self.assertEqual(metadata.status, EditionMetadata.Status.DRAFT)

        response = self.client.post(
            url,
            {
                **self._payload(commercial_title="A Study in Scarlet: Illustrated"),
                "action": "save_draft",
            },
        )
        self.assertRedirects(response, url)
        metadata.refresh_from_db()
        self.assertEqual(metadata.commercial_title, "A Study in Scarlet: Illustrated")

    def test_partial_draft_does_not_require_export_fields(self):
        url = reverse("edition_metadata_edit", args=[self.edition.id])
        response = self.client.post(
            url,
            {
                "commercial_title": "Working title",
                "slug": "Working Title",
                "action": "save_draft",
            },
        )
        self.assertRedirects(response, url)
        metadata = EditionMetadata.objects.get(edition=self.edition)
        self.assertEqual(metadata.commercial_title, "Working title")
        self.assertEqual(metadata.slug, "working-title")
        self.assertEqual(metadata.status, EditionMetadata.Status.DRAFT)

    def test_validation_marks_ready_and_keeps_seo_recommendations_non_blocking(self):
        url = reverse("edition_metadata_edit", args=[self.edition.id])
        response = self.client.post(
            url,
            {
                **self._payload(seo_title="Short SEO title"),
                "action": "validate",
            },
            follow=True,
        )
        self.assertContains(response, "recomenda-se entre 45 e 60")
        metadata = EditionMetadata.objects.get(edition=self.edition)
        self.assertEqual(metadata.status, EditionMetadata.Status.READY)
        self.assertIsNotNone(metadata.validated_at)

    def test_slug_is_normalized_and_unique(self):
        EditionMetadata.objects.create(
            edition=self.edition,
            edition_code="BOOK_0041-ENUS-EPUB-01",
            slug="A Shared Slug!",
        )
        self.assertEqual(self.edition.metadata.slug, "a-shared-slug")

        second_work = Work.objects.create(
            code="book_0042",
            title="The Sign of Four",
            original_language=self.language,
            author=self.author,
        )
        second_edition = Edition.objects.create(
            work=second_work,
            language=self.language,
            seal=self.seal,
            title=second_work.title,
        )
        response = self.client.post(
            reverse("edition_metadata_edit", args=[second_edition.id]),
            {
                "edition_code": "BOOK_0042-ENUS-EPUB-01",
                "slug": "A Shared Slug",
                "action": "save_draft",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Este slug já pertence a outra edição.")
        self.assertFalse(EditionMetadata.objects.filter(edition=second_edition).exists())

    def test_edition_code_is_normalized_and_unique(self):
        EditionMetadata.objects.create(
            edition=self.edition,
            edition_code="book_0041-enus-epub-01",
            slug="study-in-scarlet",
        )
        self.edition.metadata.refresh_from_db()
        self.assertEqual(
            self.edition.metadata.edition_code,
            "BOOK_0041-ENUS-EPUB-01",
        )

        second_work = Work.objects.create(
            code="book_0043",
            title="The Hound of the Baskervilles",
            original_language=self.language,
            author=self.author,
        )
        second_edition = Edition.objects.create(
            work=second_work,
            language=self.language,
            seal=self.seal,
            title=second_work.title,
        )
        response = self.client.post(
            reverse("edition_metadata_edit", args=[second_edition.id]),
            {
                "edition_code": "book_0041-enus-epub-01",
                "slug": "hound-baskervilles",
                "action": "save_draft",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Este código de edição já pertence a outra edição.",
        )

    def test_seo_recommendations_warn_without_blocking(self):
        metadata = EditionMetadata(
            edition=self.edition,
            edition_code="BOOK_0041-ENUS-EPUB-01",
            commercial_title="A Study in Scarlet",
            author_first_name="Arthur",
            regional_language="en-US",
            original_language="en",
            imprint_name="RinoBooks",
            publication_year=2026,
            edition_format="EPUB",
            slug="study-scarlet",
            seo_title="Short SEO title",
            seo_description="Meaningful, but shorter than the recommendation.",
            description="Full description",
            short_description="Short description",
            keywords=["Sherlock Holmes"],
            primary_category="Fiction",
            subcategory="Detective Fiction",
            theme="Deduction",
            target_audience="Mystery readers",
            cover_alt="Book cover",
            work_type="ORIGINAL_RINOBOOKS",
            legal_basis="Owned original work",
            edition_nature="Original edition",
            editorial_modifications="Original editorial production",
            authorized_territories="Worldwide",
            rights_evidence="Rights file",
            price=Decimal("19.90"),
            currency="BRL",
        )
        result = validate_metadata(metadata)
        self.assertTrue(result.is_valid)
        self.assertEqual(len(result.warnings), 2)

    def test_generic_seo_description_is_critical(self):
        metadata = EditionMetadata(
            edition=self.edition,
            seo_description="Descrição genérica",
        )
        result = validate_metadata(metadata)
        self.assertFalse(result.is_valid)
        self.assertIn(
            "A descrição SEO não pode ser um texto genérico.",
            result.errors,
        )

    def test_unsupported_regional_language_is_critical(self):
        metadata = EditionMetadata(
            edition=self.edition,
            regional_language="es-ES",
        )
        result = validate_metadata(metadata)
        self.assertIn(
            "Idioma regional não é suportado pelo contrato RinoBooks.",
            result.errors,
        )
