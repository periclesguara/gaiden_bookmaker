import json
import shutil
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from editorial.models import Contributor, Edition, EditionPipeline, Language, Seal, Work
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


class HtmlLanePreprodConvertTests(TestCase):
    def setUp(self):
        self.language = Language.objects.create(
            code="en",
            name="English",
            native_name="English",
            is_active=True,
        )
        self.author = Contributor.objects.create(name="Author HTML Test")
        self.seal = Seal.objects.create(slug="mantaquest-html", name="MantaQuest HTML")
        self.work = Work.objects.create(
            code="book_html_0001",
            title="Book HTML Test",
            original_language=self.language,
            author=self.author,
        )
        self.edition = Edition.objects.create(
            work=self.work,
            language=self.language,
            seal=self.seal,
        )
        self.template = BookEditionTemplate.objects.create(
            book_code=self.work.code,
            language="en",
            title=self.work.title,
            author_name=self.author.name,
            publication_year=2026,
            text_source_mode="html",
        )
        self.root = Path(settings.BASE_DIR).parent
        self.raw_dir = self.root / "data" / "raw" / self.work.code
        self.preprod_dir = self.root / "data" / "preprod" / self.work.code
        self.md_dir = self.root / "data" / "md" / self.work.code
        self.raw_path = self.raw_dir / f"{self.work.code}_en_raw.html"
        self.clean_path = self.preprod_dir / f"{self.work.code}_en_clean.html"
        self.report_path = self.preprod_dir / f"{self.work.code}_en_report.json"
        self.source_md_path = self.md_dir / f"{self.work.code}_en_source.md"
        self.preprod_url = reverse("pipeline_html_preprod_run", kwargs={"edition_id": self.edition.id})
        self.convert_url = reverse("pipeline_html_convert_run", kwargs={"edition_id": self.edition.id})
        self.dashboard_url = reverse("pipeline_html_dashboard", kwargs={"edition_id": self.edition.id})

    def tearDown(self):
        shutil.rmtree(self.raw_dir, ignore_errors=True)
        shutil.rmtree(self.preprod_dir, ignore_errors=True)
        shutil.rmtree(self.md_dir, ignore_errors=True)

    def _write_raw_html(self):
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        html = (
            "<html><body>"
            "<p>*** START OF THIS PROJECT GUTENBERG EBOOK ***</p>"
            "<p><b>CHAPTER IV</b></p>"
            "<p>The quick brown fox.</p>"
            "<p>*** END OF THIS PROJECT GUTENBERG EBOOK ***</p>"
            "</body></html>"
        )
        self.raw_path.write_text(html, encoding="utf-8")
        self.edition.raw_source_path = str(self.raw_path)
        self.edition.save(update_fields=["raw_source_path"])

    def test_preprod_generates_clean_and_report(self):
        self._write_raw_html()

        response = self.client.post(self.preprod_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.dashboard_url)
        self.assertTrue(self.clean_path.exists())
        self.assertTrue(self.report_path.exists())
        report = json.loads(self.report_path.read_text(encoding="utf-8"))
        self.assertTrue(report["ok_to_convert"])
        self.assertGreaterEqual(report["headings_promoted"], 1)
        pipeline_state = EditionPipeline.objects.get(edition=self.edition)
        self.assertEqual(pipeline_state.current_stage, "HTML_PREPROD_READY")

    def test_convert_blocks_when_report_not_ok(self):
        self.preprod_dir.mkdir(parents=True, exist_ok=True)
        self.clean_path.write_text("<h2>Chapter I</h2><p>Body</p>", encoding="utf-8")
        self.report_path.write_text(
            json.dumps(
                {
                    "ok_to_convert": False,
                    "errors": ["missing headings"],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )

        response = self.client.post(self.convert_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.dashboard_url)
        self.assertFalse(self.source_md_path.exists())
        pipeline_state = EditionPipeline.objects.filter(edition=self.edition).first()
        if pipeline_state is not None:
            self.assertNotEqual(pipeline_state.current_stage, "MD_SOURCE_READY")

    def test_convert_generates_source_md_when_ok(self):
        self.preprod_dir.mkdir(parents=True, exist_ok=True)
        self.clean_path.write_text(
            "<html><body><h2>Chapter I</h2><p><strong>Bold</strong> body.</p></body></html>",
            encoding="utf-8",
        )
        self.report_path.write_text(
            json.dumps(
                {
                    "ok_to_convert": True,
                    "errors": [],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )

        response = self.client.post(self.convert_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"{reverse('edition_steps', kwargs={'edition_id': self.edition.id})}?allow_html_to_common=1",
        )
        self.assertTrue(self.source_md_path.exists())
        md_text = self.source_md_path.read_text(encoding="utf-8")
        self.assertIn("Chapter I", md_text)
        pipeline_state = EditionPipeline.objects.get(edition=self.edition)
        self.assertEqual(pipeline_state.current_stage, "MD_SOURCE_READY")
