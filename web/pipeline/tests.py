import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings
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
        self.root = Path(settings.BASE_DIR).parent
        self.raw_dir = self.root / "data" / "raw" / self.work.code

    def tearDown(self):
        shutil.rmtree(self.raw_dir, ignore_errors=True)

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
        raw_path = self.raw_dir / f"{self.work.code}_en_raw.html"
        self.assertTrue(raw_path.exists())
        pipeline_state = EditionPipeline.objects.get(edition=self.edition)
        self.assertEqual(pipeline_state.current_stage, "HTML_UPLOADED")
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
        raw_path = self.raw_dir / f"{self.work.code}_en_raw.txt"
        self.assertTrue(raw_path.exists())
        pipeline_state = EditionPipeline.objects.get(edition=self.edition)
        self.assertEqual(pipeline_state.current_stage, "TXT_UPLOADED")
        mock_frontmatter.assert_called_once()

    def test_cadastro_rejects_invalid_source_format_with_400(self):
        upload = SimpleUploadedFile(
            "source.html",
            b"<html><body>Hello</body></html>",
            content_type="text/html",
        )
        payload = self._payload("html", upload)
        payload["source_format"] = "pdf"
        response = self.client.post(
            self.cadastro_url,
            data=payload,
        )
        self.assertEqual(response.status_code, 400)

    @patch("pipeline.views.kdp_mode.build_frontmatter_files")
    def test_cadastro_accepts_backcompat_post_field_names(self, mock_frontmatter):
        upload = SimpleUploadedFile(
            "source.html",
            b"<html><body>Hello</body></html>",
            content_type="text/html",
        )
        response = self.client.post(
            self.cadastro_url,
            data={
                "book_id": self.work.code,
                "lang": "en",
                "title": "Book Test",
                "author": "Author Test",
                "publication_year": 2026,
                "source_format": "html",
                "source_file": upload,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("pipeline_html_dashboard", kwargs={"edition_id": self.edition.id}),
        )
        template = BookEditionTemplate.objects.get(book_code=self.work.code, language="en")
        self.assertEqual(template.author_name, "Author Test")
        mock_frontmatter.assert_called_once()

    @patch("pipeline.views.kdp_mode.build_frontmatter_files")
    def test_cadastro_updates_existing_template_instead_of_duplicate(self, mock_frontmatter):
        BookEditionTemplate.objects.create(
            book_code=self.work.code,
            language="en",
            title="Old Title",
            author_name=self.author.name,
            publication_year=2025,
            text_source_mode="txt",
        )
        upload = SimpleUploadedFile(
            "source.html",
            b"<!doctype html><html><body>Updated</body></html>",
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
        self.assertEqual(
            BookEditionTemplate.objects.filter(book_code=self.work.code, language="en").count(),
            1,
        )
        template = BookEditionTemplate.objects.get(book_code=self.work.code, language="en")
        self.assertEqual(template.title, "Book Test")
        self.assertEqual(template.text_source_mode, "html")
        mock_frontmatter.assert_called_once()

    @patch("pipeline.views.kdp_mode.build_frontmatter_files")
    def test_editorial_autocreate_requires_existing_work(self, mock_frontmatter):
        self.edition.delete()
        self.work.delete()
        upload = SimpleUploadedFile(
            "source.html",
            b"<!doctype html><html><body>Created</body></html>",
            content_type="text/html",
        )
        response = self.client.post(
            self.cadastro_url,
            data=self._payload("html", upload),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Work/Book nao encontrado")
        self.assertFalse(Edition.objects.filter(work__code=self.work.code, language__code="en").exists())
        mock_frontmatter.assert_not_called()


class ContractIngestV1Tests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.temp_web = self.temp_root / "web"
        self.temp_web.mkdir(parents=True, exist_ok=True)
        self.settings_override = override_settings(BASE_DIR=self.temp_web)
        self.settings_override.enable()

        self.language = Language.objects.create(
            code="en",
            name="English",
            native_name="English",
            is_active=True,
        )
        self.author = Contributor.objects.create(name="Author Contract")
        self.seal = Seal.objects.create(slug="mantaquest-contract", name="MantaQuest Contract")
        self.work = Work.objects.create(
            code="book_0006",
            title="Contract Book",
            original_language=self.language,
            author=self.author,
        )
        self.edition = Edition.objects.create(
            work=self.work,
            language=self.language,
            seal=self.seal,
        )
        self.cadastro_url = reverse("book_edition_new")
        self.fixture_dir = Path(__file__).resolve().parent / "tests" / "fixtures"
        self.frontmatter_patcher = patch("pipeline.views.kdp_mode.build_frontmatter_files")
        self.mock_frontmatter = self.frontmatter_patcher.start()

    def tearDown(self):
        self.frontmatter_patcher.stop()
        self.settings_override.disable()
        self.temp_dir.cleanup()

    def _fixture_upload(self, filename: str, content_type: str) -> SimpleUploadedFile:
        payload = (self.fixture_dir / filename).read_bytes()
        return SimpleUploadedFile(filename, payload, content_type=content_type)

    def _payload(self, source_format: str, source_file: SimpleUploadedFile) -> dict:
        return {
            "book_code": self.work.code,
            "language": "en",
            "title": "Contract Book",
            "author_name": "Author Contract",
            "publication_year": 2026,
            "source_format": source_format,
            "source_file": source_file,
        }

    def test_book_code_required_returns_400(self):
        payload = self._payload("html", self._fixture_upload("minimal.html", "text/html"))
        payload.pop("book_code")
        response = self.client.post(self.cadastro_url, data=payload)
        self.assertEqual(response.status_code, 400)

    def test_language_required_returns_400(self):
        payload = self._payload("html", self._fixture_upload("minimal.html", "text/html"))
        payload.pop("language")
        response = self.client.post(self.cadastro_url, data=payload)
        self.assertEqual(response.status_code, 400)

    def test_source_format_outside_enum_returns_400(self):
        payload = self._payload("html", self._fixture_upload("minimal.html", "text/html"))
        payload["source_format"] = "docx"
        response = self.client.post(self.cadastro_url, data=payload)
        self.assertEqual(response.status_code, 400)

    def test_source_file_missing_returns_400(self):
        payload = self._payload("html", self._fixture_upload("minimal.html", "text/html"))
        payload.pop("source_file")
        response = self.client.post(self.cadastro_url, data=payload)
        self.assertEqual(response.status_code, 400)

    def test_extension_mismatch_returns_400(self):
        payload = self._payload("html", self._fixture_upload("minimal.txt", "text/plain"))
        response = self.client.post(self.cadastro_url, data=payload)
        self.assertEqual(response.status_code, 400)

    def test_alias_fields_are_accepted(self):
        response = self.client.post(
            self.cadastro_url,
            data={
                "book_id": self.work.code,
                "lang": "en",
                "title": "Contract Book",
                "author": "Author Contract",
                "publication_year": 2026,
                "source_format": "html",
                "source_file": self._fixture_upload("minimal.html", "text/html"),
            },
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("pipeline_html_dashboard", kwargs={"edition_id": self.edition.id}),
        )

    def test_html_lane_persist_stage_raw_path_and_redirect(self):
        response = self.client.post(
            self.cadastro_url,
            data=self._payload("html", self._fixture_upload("minimal.html", "text/html")),
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("pipeline_html_dashboard", kwargs={"edition_id": self.edition.id}),
        )
        template = BookEditionTemplate.objects.get(book_code=self.work.code, language="en")
        self.assertEqual(template.text_source_mode, "html")
        pipeline_state = EditionPipeline.objects.get(edition=self.edition)
        self.assertEqual(pipeline_state.current_stage, "HTML_UPLOADED")
        raw_path = self.temp_root / "data" / "raw" / self.work.code / f"{self.work.code}_en_raw.html"
        self.assertTrue(raw_path.exists())

    def test_txt_lane_persist_stage_raw_path_and_redirect(self):
        response = self.client.post(
            self.cadastro_url,
            data=self._payload("txt", self._fixture_upload("minimal.txt", "text/plain")),
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("edition_steps", kwargs={"edition_id": self.edition.id}),
        )
        template = BookEditionTemplate.objects.get(book_code=self.work.code, language="en")
        self.assertEqual(template.text_source_mode, "txt")
        pipeline_state = EditionPipeline.objects.get(edition=self.edition)
        self.assertEqual(pipeline_state.current_stage, "TXT_UPLOADED")
        raw_path = self.temp_root / "data" / "raw" / self.work.code / f"{self.work.code}_en_raw.txt"
        self.assertTrue(raw_path.exists())

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
