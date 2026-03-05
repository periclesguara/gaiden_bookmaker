from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from editorial.models import Contributor, Edition, Language, Seal, Work
from pipeline.models import BookEditionTemplate


class CadastroSourceFormatRoutingTests(TestCase):
    def setUp(self):
        self.language = Language.objects.create(
            code="en",
            name="English",
            native_name="English",
            is_active=True,
        )
        self.author = Contributor.objects.create(name="Author Test")
        self.seal = Seal.objects.create(slug="mantaquest-test", name="MantaQuest Test")
        self.work = Work.objects.create(
            code="book_9999",
            title="Book Test",
            original_language=self.language,
            author=self.author,
        )
        self.edition = Edition.objects.create(
            work=self.work,
            language=self.language,
            seal=self.seal,
        )
        self.cadastro_url = reverse("book_edition_new")

    def _payload(self, source_format: str, source_file: SimpleUploadedFile) -> dict:
        return {
            "book_code": self.work.code,
            "language": "en",
            "title": "Book Test",
            "author_name": "Author Test",
            "publication_year": 2026,
            "source_format": source_format,
            "source_file": source_file,
        }

    @patch("pipeline.views.kdp_mode.build_frontmatter_files")
    def test_cadastro_redirects_to_html_dashboard_when_source_format_is_html(self, mock_frontmatter):
        upload = SimpleUploadedFile(
            "source.html",
            b"<html><body>Hello</body></html>",
            content_type="text/html",
        )
        response = self.client.post(
            self.cadastro_url,
            data=self._payload("html", upload),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("pipeline_html_dashboard", kwargs={"edition_id": self.edition.id}),
        )
        template = BookEditionTemplate.objects.get(book_code=self.work.code, language="en")
        self.assertEqual(template.text_source_mode, "html")
        mock_frontmatter.assert_called_once()

    @patch("pipeline.views.kdp_mode.build_frontmatter_files")
    def test_cadastro_redirects_to_common_pipeline_when_source_format_is_txt(self, mock_frontmatter):
        upload = SimpleUploadedFile(
            "source.txt",
            b"plain text",
            content_type="text/plain",
        )
        response = self.client.post(
            self.cadastro_url,
            data=self._payload("txt", upload),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("edition_steps", kwargs={"edition_id": self.edition.id}),
        )
        template = BookEditionTemplate.objects.get(book_code=self.work.code, language="en")
        self.assertEqual(template.text_source_mode, "txt")
        mock_frontmatter.assert_called_once()
