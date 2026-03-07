import io
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

from editorial.models import Contributor, Edition, EditionPipeline, EditionText, Language, Seal, Work
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
        self.normalized_md_path = self.md_dir / f"{self.work.code}_en_normalized.md"
        self.canonical_md_path = self.md_dir / f"{self.work.code}_en_canonical.md"
        self.reupload_url = reverse("pipeline_html_reupload_run", kwargs={"edition_id": self.edition.id})
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

    def test_html_dashboard_shows_step1_html_uploaded(self):
        EditionPipeline.objects.create(edition=self.edition, current_stage="HTML_UPLOADED")

        response = self.client.get(self.dashboard_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Step 1 - HTML_UPLOADED")
        self.assertContains(response, "RAW HTML")
        self.assertContains(response, f"{self.work.code}_en_raw.html")

    def test_reupload_resets_stage_and_cleans_stale_artifacts(self):
        self._write_raw_html()
        self.preprod_dir.mkdir(parents=True, exist_ok=True)
        self.md_dir.mkdir(parents=True, exist_ok=True)
        self.clean_path.write_text("<html><body><h2>Old</h2></body></html>", encoding="utf-8")
        self.report_path.write_text('{"ok_to_convert": true}', encoding="utf-8")
        self.source_md_path.write_text("# old md", encoding="utf-8")
        self.normalized_md_path.write_text("# old normalized", encoding="utf-8")
        self.canonical_md_path.write_text("# old canonical", encoding="utf-8")
        EditionPipeline.objects.update_or_create(
            edition=self.edition,
            defaults={"current_stage": "MD_SOURCE_READY"},
        )

        response = self.client.post(
            self.reupload_url,
            data={
                "source_file": SimpleUploadedFile(
                    "replacement.html",
                    b"<html><body><h2>New Raw</h2><p>Body</p></body></html>",
                    content_type="text/html",
                )
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.dashboard_url)
        self.assertTrue(self.raw_path.exists())
        self.assertIn("New Raw", self.raw_path.read_text(encoding="utf-8"))
        self.assertFalse(self.clean_path.exists())
        self.assertFalse(self.report_path.exists())
        self.assertFalse(self.source_md_path.exists())
        self.assertFalse(self.normalized_md_path.exists())
        self.assertFalse(self.canonical_md_path.exists())
        self.edition.refresh_from_db()
        self.assertEqual(self.edition.raw_source_path, str(self.raw_path))
        texts = EditionText.objects.get(edition=self.edition)
        self.assertEqual(texts.raw_path, str(self.raw_path))
        pipeline_state = EditionPipeline.objects.get(edition=self.edition)
        self.assertEqual(pipeline_state.current_stage, "HTML_UPLOADED")
        self.assertIn("reupload", pipeline_state.last_log)

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


class HeadingCleanerGateTests(TestCase):
    def setUp(self):
        self.language = Language.objects.create(
            code="en",
            name="English",
            native_name="English",
            is_active=True,
        )
        self.author = Contributor.objects.create(name="Author Gate Test")
        self.seal = Seal.objects.create(slug="mantaquest-gate", name="MantaQuest Gate")
        self.work = Work.objects.create(
            code="book_9001",
            title="Book Gate Test",
            original_language=self.language,
            author=self.author,
        )
        self.edition = Edition.objects.create(
            work=self.work,
            language=self.language,
            seal=self.seal,
        )
        BookEditionTemplate.objects.create(
            book_code=self.work.code,
            language="en",
            title=self.work.title,
            author_name=self.author.name,
            publication_year=2026,
            text_source_mode="html",
        )

        self.root = Path(settings.BASE_DIR).parent
        self.book_code = self.work.code
        self.source_md_dir = self.root / "data" / "md" / self.book_code
        self.source_md_path = self.source_md_dir / f"{self.book_code}_en_source.md"
        self.normalized_path = self.root / "data" / "normalized" / f"{self.book_code}_en_v2.txt"
        self.split_dir = self.root / "data" / "chunks" / "book_9001" / "split_01"
        self.cleaner_dir = self.root / "data" / "chunks" / "book_9001" / "heading_cleaner"

        self.source_md_dir.mkdir(parents=True, exist_ok=True)
        self.source_md_path.write_text(
            (
                "# Book Gate Test\n\n"
                "CHAPTER I\n"
                + ("lorem ipsum " * 1400)
                + "\n\nCHAPTER II\n"
                + ("dolor sit amet " * 1400)
                + "\n"
            ),
            encoding="utf-8",
        )

        self.steps_url = (
            f"{reverse('edition_steps', kwargs={'edition_id': self.edition.id})}?allow_html_to_common=1"
        )
        self.heading_url = reverse("pipeline_heading_cleaner_run", kwargs={"edition_id": self.edition.id})

    def tearDown(self):
        shutil.rmtree(self.source_md_dir, ignore_errors=True)
        if self.normalized_path.exists():
            self.normalized_path.unlink()
        shutil.rmtree(self.root / "data" / "chunks" / "book_9001", ignore_errors=True)

    def test_heading_cleaner_button_visible(self):
        response = self.client.get(self.steps_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rodar HeadingCleaner")
        self.assertContains(response, self.heading_url)

    def test_heading_cleaner_run_creates_outputs(self):
        response = self.client.post(self.heading_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.steps_url)
        self.assertTrue(self.normalized_path.exists())
        self.assertTrue(self.split_dir.exists())
        self.assertTrue(any(self.split_dir.glob("*.txt")))
        self.assertTrue(self.cleaner_dir.exists())
        self.assertTrue(any(self.cleaner_dir.glob("*.clean.txt")))

    def test_translate_disabled_without_heading_clean(self):
        response = self.client.get(self.steps_url)
        html = response.content.decode("utf-8")

        self.assertFalse(bool(response.context["can_translate"]))
        self.assertRegex(html, r'id="btn_translate"[^>]*disabled')
        self.assertIn("translate_prereq_msg", html)

    def test_translate_enabled_with_heading_clean(self):
        self.client.post(self.heading_url)
        response = self.client.get(self.steps_url)
        html = response.content.decode("utf-8")

        self.assertTrue(bool(response.context["can_translate"]))
        self.assertNotRegex(html, r'id="btn_translate"[^>]*disabled')
        self.assertNotIn("translate_prereq_msg", html)

    def test_pipeline01_step_order_is_fixed(self):
        response = self.client.get(self.steps_url)
        html = response.content.decode("utf-8")

        expected = [
            "1) Normalize",
            "2) Split/Chunk",
            "3) HeadingCleaner (OpenAI)",
            "4) Translate (script + JSON)",
            "5) Refine (Aldebaran)",
            "6) Merge/Finalize",
        ]
        positions = [html.find(item) for item in expected]
        self.assertTrue(all(pos >= 0 for pos in positions))
        self.assertEqual(positions, sorted(positions))

    def test_translate_disabled_without_heading_cleaner(self):
        response = self.client.get(self.steps_url)
        html = response.content.decode("utf-8")

        self.assertRegex(html, r'id="btn_translate"[^>]*disabled')

    def test_translate_shows_contract_path(self):
        response = self.client.get(self.steps_url)

        self.assertContains(response, "gaiden/contracts/en_modern_2025.json")

    def test_refine_disabled_without_translate_outputs(self):
        self.client.post(self.heading_url)
        response = self.client.get(self.steps_url)
        html = response.content.decode("utf-8")

        self.assertRegex(html, r'id="btn_refine"[^>]*disabled')

    def test_heading_cleaner_smoke_keeps_step_order(self):
        self.client.post(self.heading_url)
        response = self.client.get(self.steps_url)
        html = response.content.decode("utf-8")

        expected = [
            "1) Normalize",
            "2) Split/Chunk",
            "3) HeadingCleaner (OpenAI)",
            "4) Translate (script + JSON)",
            "5) Refine (Aldebaran)",
            "6) Merge/Finalize",
        ]
        positions = [html.find(item) for item in expected]
        self.assertTrue(all(pos >= 0 for pos in positions))
        self.assertEqual(positions, sorted(positions))


class EditorialImagePipelineContractTests(TestCase):
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
        self.author = Contributor.objects.create(name="Author Images Contract")
        self.seal = Seal.objects.create(slug="mantaquest-images", name="MantaQuest Images")
        self.work = Work.objects.create(
            code="book_0101",
            title="Image Contract Book",
            original_language=self.language,
            author=self.author,
        )
        self.edition = Edition.objects.create(
            work=self.work,
            language=self.language,
            seal=self.seal,
        )
        BookEditionTemplate.objects.create(
            book_code=self.work.code,
            language="en",
            title=self.work.title,
            author_name=self.author.name,
            publication_year=2026,
            text_source_mode="html",
        )

        self.root = Path(settings.BASE_DIR).parent
        self.build_dir = self.root / "data" / "builds" / self.work.code / "en"
        self.images_dir = self.root / "data" / "images" / self.work.code / "en"
        self.assets_dir = self.build_dir / "assets" / "images"
        self.pre_edition_path = self.build_dir / "BOOK.PRE_EDITION.md"
        self.steps_url = (
            f"{reverse('edition_steps', kwargs={'edition_id': self.edition.id})}?allow_html_to_common=1"
        )

        self.build_dir.mkdir(parents=True, exist_ok=True)
        self.pre_edition_path.write_text(
            (
                "# Chapter 01 - The Adventure of the Empty House\n\n"
                "Body of chapter one.\n\n"
                "# Chapter 02 - The Adventure of the Norwood Builder\n\n"
                "Body of chapter two.\n"
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.settings_override.disable()
        self.temp_dir.cleanup()

    def _image_upload(self, name: str) -> SimpleUploadedFile:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (10, 10), "red").save(buffer, format="PNG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

    def test_image_pipeline_controls_are_mandatory_in_common_pipeline(self):
        response = self.client.get(self.steps_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Salvar e converter imagens")
        self.assertContains(response, "Upload ZIP images")
        self.assertContains(response, "Consolidate internal images")
        self.assertContains(response, "Insert page headlines")
        self.assertContains(response, "Insert image placeholders")
        self.assertContains(response, "Apply images to PRE_EDITION")
        self.assertContains(response, "?allow_html_to_common=1#transformacao-editorial")

    def test_upload_multiple_images_is_persisted_as_jpg_in_active_images_dir(self):
        response = self.client.post(
            self.steps_url,
            data={
                "action": "upload_images_files",
                "images_files": [
                    self._image_upload("01.png"),
                    self._image_upload("02.png"),
                ],
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"{reverse('edition_steps', kwargs={'edition_id': self.edition.id})}?allow_html_to_common=1#transformacao-editorial",
        )
        self.assertTrue((self.images_dir / "01.jpg").exists())
        self.assertTrue((self.images_dir / "02.jpg").exists())

    def test_insert_and_apply_images_are_contractually_preserved(self):
        self.client.post(
            self.steps_url,
            data={
                "action": "upload_images_files",
                "images_files": [
                    self._image_upload("01.png"),
                    self._image_upload("02.png"),
                ],
            },
        )

        response = self.client.post(
            self.steps_url,
            data={"action": "insert_images"},
        )
        self.assertEqual(response.status_code, 302)
        md_with_placeholders = self.pre_edition_path.read_text(encoding="utf-8")
        self.assertIn("{{IMAGE:CH01:01}}", md_with_placeholders)
        self.assertIn("{{IMAGE:CH02:01}}", md_with_placeholders)

        response = self.client.post(
            self.steps_url,
            data={"action": "apply_images"},
        )
        self.assertEqual(response.status_code, 302)
        final_md = self.pre_edition_path.read_text(encoding="utf-8")
        self.assertIn("![CH01:01](assets/images/ch01_01_01.jpg)", final_md)
        self.assertIn("![CH02:01](assets/images/ch02_01_02.jpg)", final_md)
        self.assertTrue((self.assets_dir / "ch01_01_01.jpg").exists())
        self.assertTrue((self.assets_dir / "ch02_01_02.jpg").exists())
