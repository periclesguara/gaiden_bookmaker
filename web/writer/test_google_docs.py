from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from web.writer.models import SourceDocument
from web.writer.services.google_docs import (
    canonical_google_doc_url,
    extract_document_id,
)
from web.writer.services.google_docs import _to_text
from web.writer.services.source_loader import load_source
from web.writer.services.sources import register_google_doc


class GoogleDocsParsingTests(SimpleTestCase):
    def test_extracts_raw_id_and_url(self):
        self.assertEqual(extract_document_id("document_ABC-123456"), "document_ABC-123456")
        self.assertEqual(
            extract_document_id("https://docs.google.com/document/d/document_ABC-123456/edit"),
            "document_ABC-123456",
        )
        self.assertEqual(
            canonical_google_doc_url("document_ABC-123456"),
            "https://docs.google.com/document/d/document_ABC-123456/edit",
        )

    def test_rejects_malformed_url(self):
        with self.assertRaises(ValueError):
            extract_document_id("https://example.com/document/d/not-google")

    def test_converts_google_paragraph_styles_to_markdown(self):
        text, structure, _ = _to_text({"content": [
            {"paragraph": {"paragraphStyle": {"namedStyleType": "TITLE"}, "elements": [{"textRun": {"content": "Title\n"}}]}},
            {"paragraph": {"paragraphStyle": {"namedStyleType": "HEADING_2"}, "elements": [{"textRun": {"content": "Section\n"}}]}},
            {"paragraph": {"paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}, "elements": [{"textRun": {"content": "Body\n"}}]}},
        ]})
        self.assertEqual(text, "# Title\n\n## Section\n\nBody\n")
        self.assertEqual(len(structure), 3)


class GoogleDocsSourceTests(TestCase):
    def test_registration_is_idempotent(self):
        value = "https://docs.google.com/document/d/document_ABC-123456/edit"
        first = register_google_doc(value)
        second = register_google_doc(value)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(SourceDocument.objects.filter(source_type="GOOGLE_DOC").count(), 1)

    def test_local_loader_preserves_utf8_text(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "source.txt"
            path.write_text("Olá, Writer\n", encoding="utf-8")
            document = SourceDocument(source_path=str(path), filename=path.name)
            raw = load_source(document)
        self.assertEqual(raw.text, "Olá, Writer\n")
        self.assertEqual(raw.source_type, SourceDocument.SourceType.LOCAL_FILE)

    @patch("web.writer.services.google_docs.load_google_doc")
    def test_loader_dispatches_google_docs(self, load_google_doc):
        from web.writer.services.source_loader import RawDocument

        expected = RawDocument(source_type="GOOGLE_DOC", text="# Title\n")
        load_google_doc.return_value = expected
        document = SourceDocument(source_type=SourceDocument.SourceType.GOOGLE_DOC, external_id="document_ABC-123456")
        self.assertIs(load_source(document), expected)
        load_google_doc.assert_called_once_with(document)
