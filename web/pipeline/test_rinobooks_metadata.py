import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse

from editorial.models import (
    Contributor,
    Edition,
    EditionMetadata,
    Language,
    Seal,
    Work,
)
from pipeline.services import book_manifest
from pipeline.services.rinobooks_publish import (
    RinoBooksPublishError,
    publish_edition,
)


class RinoBooksMetadataContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.language = Language.objects.create(
            code="en",
            name="English",
            native_name="English",
        )
        cls.seal = Seal.objects.create(slug="rinobooks", name="RinoBooks")
        cls.author = Contributor.objects.create(name="Epictetus")
        cls.work = Work.objects.create(
            code="BOOK_0001",
            title="The Enchiridion",
            original_language=cls.language,
            author=cls.author,
            year=125,
        )
        cls.edition = Edition.objects.create(
            work=cls.work,
            language=cls.language,
            seal=cls.seal,
            title="The Enchiridion",
            subtitle="Modern English Edition",
            author="Epictetus",
            publication_year=2026,
            language_code="en",
        )

    def _metadata(self, edition=None, **overrides):
        values = {
            "edition": edition or self.edition,
            "edition_code": "BOOK_0001-ENUS-EPUB-01",
            "commercial_title": "The Enchiridion",
            "subtitle": "Modern English Edition",
            "original_title": "Enchiridion",
            "author_first_name": "Epictetus",
            "regional_language": "en-US",
            "original_language": "grc",
            "imprint_name": "RinoBooks",
            "collection_name": "Stoic Classics",
            "edition_number": 1,
            "publication_year": 2026,
            "edition_format": "EPUB",
            "slug": "the-enchiridion-modern-english-edition",
            "seo_title": "The Enchiridion: Modern English Edition | RinoBooks",
            "seo_description": (
                "A clear modern English adaptation of Epictetus' practical Stoic "
                "manual, prepared with helpful editorial context for today's readers."
            ),
            "description": "A complete commercial description.",
            "short_description": "A practical Stoic manual in modern English.",
            "keywords": ["Epictetus", "Stoicism", "Stoic philosophy", "Enchiridion"],
            "primary_category": "Philosophy",
            "subcategory": "Stoicism",
            "theme": "Practical philosophy",
            "target_audience": "Readers of classical philosophy",
            "cover_alt": "Cover of The Enchiridion by Epictetus",
            "work_type": "PUBLIC_DOMAIN",
            "base_work_year": 125,
            "consulted_source": "Verified public-domain Greek and English sources.",
            "legal_basis": "Public-domain source.",
            "edition_nature": "Modern English adaptation",
            "editorial_modifications": "Modernized language and original editorial notes.",
            "authorized_territories": "Worldwide where public-domain status applies.",
            "blocked_territories": "Territories pending legal review.",
            "rights_evidence": "Rights review worksheet.",
            "price": Decimal("19.90"),
            "currency": "BRL",
            "sample_title": "Opening",
            "sample_content": "Some things are in our control...",
            "promotional_images": ["https://assets.example.invalid/enchiridion.jpg"],
        }
        values.update(overrides)
        return EditionMetadata.objects.create(**values)

    def test_manifest_serializes_canonical_storefront_and_legacy_keys(self):
        self._metadata()
        manifest = book_manifest.build_manifest(
            self.edition,
            export_user="tester",
            epubcheck_status="pass",
        ).to_dict()
        fixture_manifest = json.loads(json.dumps(manifest))
        fixture_manifest["edition_id"] = 1
        fixture_manifest["export_date"] = "2026-08-13T00:00:00Z"
        fixture_manifest["export_user"] = "contract-fixture"
        fixture_path = (
            Path(__file__).with_name("contract_fixtures") / "manifest_v2.json"
        )
        expected_fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(fixture_manifest, expected_fixture)

        self.assertEqual(manifest["edition_id"], self.edition.id)
        self.assertEqual(manifest["book_code"], "BOOK_0001")
        self.assertEqual(manifest["edition_code"], "BOOK_0001-ENUS-EPUB-01")
        self.assertEqual(manifest["language"], "en-US")
        self.assertEqual(manifest["edition_type"], "EPUB")
        self.assertEqual(manifest["status"], "DRAFT")
        self.assertEqual(manifest["contract_version"], 2)
        self.assertEqual(manifest["storefront"]["price_cents"], 1990)
        self.assertEqual(manifest["storefront"]["author"]["first_name"], "Epictetus")
        self.assertEqual(manifest["storefront"]["primary_category"], "Philosophy")
        for legacy_key in ("text_source", "md_files", "build", "export", "export_user"):
            self.assertIn(legacy_key, manifest)

    def test_manifest_remains_serializable_without_new_metadata(self):
        manifest = book_manifest.build_manifest(self.edition).to_dict()
        payload = json.loads(json.dumps(manifest))
        self.assertIsNone(payload["edition_code"])
        self.assertEqual(payload["language"], "en")
        self.assertIn("text_source", payload)
        self.assertIn("storefront", payload)

    def test_send_stops_before_artifacts_and_network_on_critical_metadata_errors(self):
        EditionMetadata.objects.create(
            edition=self.edition,
            edition_code="BOOK_0001-ENUS-EPUB-01",
            slug="enchiridion",
        )
        session = Mock()
        with (
            patch(
                "pipeline.services.rinobooks_publish.kdp_mode.run_epubcheck_for_edition"
            ) as epubcheck,
            self.assertRaisesRegex(RinoBooksPublishError, "Metadata validation failed"),
        ):
            publish_edition(self.edition, session=session)
        epubcheck.assert_not_called()
        session.post.assert_not_called()

    def test_send_requires_valid_epub_and_cover(self):
        self._metadata()
        session = Mock()
        with patch(
            "pipeline.services.rinobooks_publish.kdp_mode.run_epubcheck_for_edition",
            side_effect=FileNotFoundError("missing EPUB"),
        ):
            with self.assertRaisesRegex(RinoBooksPublishError, "EPUB validation failed"):
                publish_edition(self.edition, session=session)
        session.post.assert_not_called()

    def test_send_requires_cover_after_epub_validation(self):
        self._metadata()
        session = Mock()
        with TemporaryDirectory() as temp_dir:
            epub = Path(temp_dir) / "BOOK.EPUB3"
            epub.write_bytes(b"epub")
            with patch(
                "pipeline.services.rinobooks_publish.kdp_mode.run_epubcheck_for_edition",
                return_value=epub,
            ):
                with self.assertRaisesRegex(RinoBooksPublishError, "Cover not found"):
                    publish_edition(self.edition, session=session)
        session.post.assert_not_called()

    def test_send_accepts_only_draft_response(self):
        self._metadata()
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            epub = root / "BOOK.EPUB3"
            cover = root / "cover.jpg"
            epub.write_bytes(b"epub")
            cover.write_bytes(b"cover")
            self.edition.cover_filepath = str(cover)
            self.edition.save(update_fields=["cover_filepath"])

            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {
                "contract_version": 2,
                "catalog_edition_id": 7,
                "status": "PUBLISHED",
            }
            session = Mock()
            session.post.return_value = response

            with (
                override_settings(BASE_DIR=root / "web"),
                patch.dict(
                    "os.environ",
                    {
                        "RINOBOOKS_PUBLISH_URL": "https://rinobooks.example.invalid",
                        "RINOBOOKS_PUBLISH_TOKEN": "x" * 32,
                    },
                ),
                patch(
                    "pipeline.services.rinobooks_publish.kdp_mode.run_epubcheck_for_edition",
                    return_value=epub,
                ),
                self.assertRaisesRegex(
                    RinoBooksPublishError,
                    "invalid draft response",
                ),
            ):
                publish_edition(self.edition, session=session)

            sent_manifest = json.loads(session.post.call_args.kwargs["data"]["manifest"])
            self.assertEqual(sent_manifest["status"], "DRAFT")

    def test_valid_package_is_sent_as_draft(self):
        self._metadata()
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            epub = root / "BOOK.EPUB3"
            cover = root / "cover.jpg"
            epub.write_bytes(b"epub")
            cover.write_bytes(b"cover")
            self.edition.cover_filepath = str(cover)
            self.edition.save(update_fields=["cover_filepath"])

            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {
                "contract_version": 2,
                "catalog_edition_id": 7,
                "status": "DRAFT",
                "duplicate": False,
                "replaced_draft": False,
                "result": "created",
            }
            session = Mock()
            session.post.return_value = response

            with (
                override_settings(BASE_DIR=root / "web"),
                patch.dict(
                    "os.environ",
                    {
                        "RINOBOOKS_PUBLISH_URL": "https://rinobooks.example.invalid",
                        "RINOBOOKS_PUBLISH_TOKEN": "x" * 32,
                    },
                ),
                patch(
                    "pipeline.services.rinobooks_publish.kdp_mode.run_epubcheck_for_edition",
                    return_value=epub,
                ),
            ):
                draft = publish_edition(self.edition, session=session)

            self.assertEqual(draft.status, "DRAFT")
            self.assertEqual(draft.edition_id, 7)
            self.assertEqual(
                session.post.call_args.args[0],
                "https://rinobooks.example.invalid/api/gaiden/editions",
            )
            self.assertEqual(session.post.call_args.kwargs["files"]["epub"][0], "BOOK.EPUB3")
            sent_manifest = json.loads(
                session.post.call_args.kwargs["data"]["manifest"]
            )
            self.assertEqual(sent_manifest["export"]["epub"], str(epub))

    def test_sender_accepts_legacy_v1_receiver_response_during_transition(self):
        self._metadata()
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            epub = root / "BOOK.EPUB3"
            cover = root / "cover.jpg"
            epub.write_bytes(b"epub")
            cover.write_bytes(b"cover")
            self.edition.cover_filepath = str(cover)
            self.edition.save(update_fields=["cover_filepath"])

            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"edition_id": 9, "status": "DRAFT"}
            session = Mock()
            session.post.return_value = response

            with (
                override_settings(BASE_DIR=root / "web"),
                patch.dict(
                    "os.environ",
                    {
                        "RINOBOOKS_PUBLISH_URL": "https://rinobooks.example.invalid",
                        "RINOBOOKS_PUBLISH_TOKEN": "x" * 32,
                    },
                ),
                patch(
                    "pipeline.services.rinobooks_publish.kdp_mode.run_epubcheck_for_edition",
                    return_value=epub,
                ),
            ):
                draft = publish_edition(self.edition, session=session)

            self.assertEqual(draft.edition_id, 9)

    def test_sender_rejects_unknown_receiver_contract_version(self):
        self._metadata()
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            epub = root / "BOOK.EPUB3"
            cover = root / "cover.jpg"
            epub.write_bytes(b"epub")
            cover.write_bytes(b"cover")
            self.edition.cover_filepath = str(cover)
            self.edition.save(update_fields=["cover_filepath"])

            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {
                "contract_version": 3,
                "catalog_edition_id": 7,
                "status": "DRAFT",
            }
            session = Mock()
            session.post.return_value = response

            with (
                override_settings(BASE_DIR=root / "web"),
                patch.dict(
                    "os.environ",
                    {
                        "RINOBOOKS_PUBLISH_URL": "https://rinobooks.example.invalid",
                        "RINOBOOKS_PUBLISH_TOKEN": "x" * 32,
                    },
                ),
                patch(
                    "pipeline.services.rinobooks_publish.kdp_mode.run_epubcheck_for_edition",
                    return_value=epub,
                ),
                self.assertRaisesRegex(
                    RinoBooksPublishError,
                    "unsupported contract version",
                ),
            ):
                publish_edition(self.edition, session=session)

    def test_epub_export_is_blocked_until_metadata_is_valid(self):
        session_url = reverse(
            "pipeline_run_edition_step",
            args=[self.edition.id, "export_epub"],
        )
        with patch("pipeline.views.kdp_mode.build_epub_for_edition") as build_epub:
            response = self.client.post(session_url)
        self.assertRedirects(response, reverse("edition_steps", args=[self.edition.id]))
        build_epub.assert_not_called()

    def test_existing_epub_export_path_runs_after_metadata_validation(self):
        self._metadata()
        session_url = reverse(
            "pipeline_run_edition_step",
            args=[self.edition.id, "export_epub"],
        )
        with patch(
            "pipeline.views.kdp_mode.build_epub_for_edition",
            return_value=Path("data/builds/BOOK.EPUB3"),
        ) as build_epub:
            response = self.client.post(session_url)
        self.assertRedirects(response, reverse("edition_steps", args=[self.edition.id]))
        build_epub.assert_called_once_with(self.edition)
