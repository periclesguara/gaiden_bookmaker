import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from pipeline.services.rinobooks_publish import (
    RinoBooksPublishError,
    publish_edition,
)


class _Manifest:
    def to_dict(self):
        return {
            "edition_id": 41,
            "book_code": "book_0041",
            "language": "pt-br",
            "edition_type": "EPUB",
            "imprint_name": "RinoBooks",
            "export_date": "2026-08-10T00:00:00+00:00",
        }


class RinoBooksPublishTests(SimpleTestCase):
    def _edition(self, cover_path):
        return SimpleNamespace(
            id=41,
            title="Epicteto",
            subtitle="Manual para a vida",
            author="Epicteto",
            about_edition_text="Edição moderna em português.",
            copyright_text="Texto original em domínio público.",
            cover_filepath=str(cover_path),
            imprint_name="RinoBooks",
            work=SimpleNamespace(
                code="book_0041",
                title="Epicteto",
                author=SimpleNamespace(name="Epicteto"),
            ),
            language=SimpleNamespace(code="pt-br"),
        )

    @patch.dict(
        "os.environ",
        {
            "RINOBOOKS_PUBLISH_URL": "https://rinobooks.example",
            "RINOBOOKS_PUBLISH_TOKEN": "test-only-token",
        },
        clear=False,
    )
    def test_sends_validated_epub_cover_and_storefront_manifest(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cover = root / "cover.jpg"
            epub = root / "book.epub"
            cover.write_bytes(b"jpeg")
            epub.write_bytes(b"epub")

            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {
                "edition_id": 7,
                "status": "DRAFT",
                "duplicate": False,
            }
            session = Mock()
            session.post.return_value = response

            with (
                patch(
                    "pipeline.services.rinobooks_publish.kdp_mode.run_epubcheck_for_edition",
                    return_value=epub,
                ) as epubcheck,
                patch(
                    "pipeline.services.rinobooks_publish.book_manifest.build_manifest",
                    return_value=_Manifest(),
                ),
            ):
                result = publish_edition(self._edition(cover), session=session)

            epubcheck.assert_called_once()
            self.assertEqual(result.edition_id, 7)
            self.assertEqual(result.status, "DRAFT")

            call = session.post.call_args
            self.assertEqual(
                call.args[0],
                "https://rinobooks.example/api/gaiden/editions",
            )
            self.assertEqual(
                call.kwargs["headers"]["Authorization"],
                "Bearer test-only-token",
            )
            manifest = json.loads(call.kwargs["data"]["manifest"])
            self.assertEqual(manifest["storefront"]["title"], "Epicteto")
            self.assertEqual(manifest["storefront"]["slug"], "epicteto-pt-br")
            self.assertEqual(call.kwargs["files"]["epub"][0], "book.epub")
            self.assertEqual(call.kwargs["files"]["cover"][0], "cover.jpg")

    def test_stops_before_network_when_epubcheck_fails(self):
        session = Mock()
        edition = self._edition("/missing/cover.jpg")

        with patch(
            "pipeline.services.rinobooks_publish.kdp_mode.run_epubcheck_for_edition",
            side_effect=RuntimeError("EPUBCheck failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "EPUBCheck failed"):
                publish_edition(edition, session=session)

        session.post.assert_not_called()

    @patch.dict(
        "os.environ",
        {
            "RINOBOOKS_PUBLISH_URL": "http://insecure.example",
            "RINOBOOKS_PUBLISH_TOKEN": "test-only-token",
        },
        clear=False,
    )
    def test_rejects_non_https_endpoint(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cover = root / "cover.jpg"
            epub = root / "book.epub"
            cover.write_bytes(b"jpeg")
            epub.write_bytes(b"epub")

            with (
                patch(
                    "pipeline.services.rinobooks_publish.kdp_mode.run_epubcheck_for_edition",
                    return_value=epub,
                ),
                patch(
                    "pipeline.services.rinobooks_publish.book_manifest.build_manifest",
                    return_value=_Manifest(),
                ),
                self.assertRaisesRegex(
                    RinoBooksPublishError,
                    "must be an HTTPS URL",
                ),
            ):
                publish_edition(self._edition(cover), session=Mock())
