import io
import json
import re
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from editorial.models import Contributor, Edition, EditionPipeline, EditionText, Language, Seal, Work
from pipeline.forms import normalize_book_code_input
from pipeline.models import BookEditionTemplate


class _FakeResponsesAPI:
    def __init__(self, output_text: str):
        self.output_text = output_text

    def create(self, **kwargs):
        class _Resp:
            pass

        resp = _Resp()
        resp.output_text = self.output_text
        return resp


class _FakeOpenAIClient:
    def __init__(self, output_text: str):
        self.responses = _FakeResponsesAPI(output_text)


class _SlowResponsesAPI:
    def __init__(self, output_text: str, delay: float):
        self.output_text = output_text
        self.delay = delay

    def create(self, **kwargs):
        time.sleep(self.delay)

        class _Resp:
            pass

        resp = _Resp()
        resp.output_text = self.output_text
        return resp


class _SlowOpenAIClient:
    def __init__(self, output_text: str, delay: float):
        self.responses = _SlowResponsesAPI(output_text, delay)


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

    def test_cadastro_get_is_canonical_entrypoint_for_new_html_books(self):
        response = self.client.get(self.cadastro_url)
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cadastro")
        self.assertContains(response, "Salvar Cadastro")
        self.assertContains(response, "Enviar Arquivo")
        self.assertContains(response, "Pre-producao HTML")
        self.assertContains(response, "Inicio / Cadastro")
        self.assertContains(response, "Pipeline")
        self.assertContains(response, "Steps")
        self.assertContains(response, "Frontmatter")
        self.assertIn('data-contract="pipeline_ingest_v1"', html)
        self.assertIn('data-contract-entrypoint="book_edition_new"', html)
        self.assertIn('data-contract-html-next="pipeline_html_dashboard"', html)

    def test_root_redirects_to_canonical_cadastro_entrypoint(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("book_edition_new"))

    def test_cadastro_shows_continue_selector_for_existing_books(self):
        work = Work.objects.create(
            code="book_0001",
            title="Continue Book",
            original_language=self.language,
            author=self.author,
        )
        edition = Edition.objects.create(
            work=work,
            language=self.language,
            seal=self.seal,
            title="Continue Book",
        )

        response = self.client.get(self.cadastro_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Continuar Livro Existente")
        self.assertContains(response, "01 - book_0001 [en] - Continue Book")
        self.assertContains(response, reverse("edition_steps", kwargs={"edition_id": edition.id}))

    def test_cadastro_shows_book_012_and_uses_stage_specific_continue_url(self):
        work = Work.objects.create(
            code="book_012",
            title="Conan - Shadows in Moonlight",
            original_language=self.language,
            author=self.author,
        )
        edition = Edition.objects.create(
            work=work,
            language=self.language,
            seal=self.seal,
            title="Conan - Shadows in Moonlight",
        )
        BookEditionTemplate.objects.create(
            book_code="book_012",
            language="en",
            title="Conan - Shadows in Moonlight",
            author_name="Author Test",
            publication_year=2026,
            text_source_mode="html",
        )
        EditionPipeline.objects.create(
            edition=edition,
            current_stage="HTML_UPLOADED",
        )

        response = self.client.get(self.cadastro_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "12 - book_012 [en] - Conan - Shadows in Moonlight")
        self.assertContains(
            response,
            reverse("pipeline_html_dashboard", kwargs={"edition_id": edition.id}),
        )

    def test_cadastro_continue_selector_routes_html_books_after_html_lane_to_common_steps(self):
        work = Work.objects.create(
            code="book_013",
            title="Continue Common Steps",
            original_language=self.language,
            author=self.author,
        )
        edition = Edition.objects.create(
            work=work,
            language=self.language,
            seal=self.seal,
            title="Continue Common Steps",
        )
        BookEditionTemplate.objects.create(
            book_code="book_013",
            language="en",
            title="Continue Common Steps",
            author_name="Author Test",
            publication_year=2026,
            text_source_mode="html",
        )
        EditionPipeline.objects.create(
            edition=edition,
            current_stage="MERGED",
        )

        response = self.client.get(self.cadastro_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "13 - book_013 [en] - Continue Common Steps")
        self.assertContains(
            response,
            reverse("edition_steps", kwargs={"edition_id": edition.id}),
        )

    def test_frontmatter_page_renders_workspace_nav_for_existing_edition(self):
        BookEditionTemplate.objects.update_or_create(
            book_code=self.work.code,
            language="en",
            defaults={
                "title": self.work.title,
                "author_name": self.author.name,
                "publication_year": 2026,
                "text_source_mode": "html",
            },
        )

        response = self.client.get(
            reverse("frontmatter_template_edit", kwargs={"book_code": self.work.code, "language": "en"})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Inicio / Cadastro")
        self.assertContains(response, "Pipeline")
        self.assertContains(
            response,
            reverse("pipeline_html_dashboard", kwargs={"edition_id": self.edition.id}),
        )
        self.assertContains(
            response,
            f"{reverse('edition_steps', kwargs={'edition_id': self.edition.id})}?allow_html_to_common=1",
        )
        self.assertContains(
            response,
            reverse("frontmatter_template_edit", kwargs={"book_code": self.work.code, "language": "en"}),
        )

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
    def test_cadastro_can_save_metadata_first_and_upload_file_afterwards(self, mock_frontmatter):
        save_response = self.client.post(
            self.cadastro_url,
            data={
                "action": "save_metadata",
                "book_code": self.work.code,
                "language": "en",
                "title": "Book Test",
                "author_name": "Author Test",
                "publication_year": 2026,
                "source_format": "html",
            },
            follow=False,
        )

        self.assertEqual(save_response.status_code, 302)
        self.assertEqual(
            save_response.url,
            reverse("book_edition_edit", kwargs={"book_code": self.work.code, "language": "en"}),
        )
        template = BookEditionTemplate.objects.get(book_code=self.work.code, language="en")
        self.assertEqual(template.text_source_mode, "html")
        raw_path = self.raw_dir / f"{self.work.code}_en_raw.html"
        self.assertFalse(raw_path.exists())

        upload_response = self.client.post(
            reverse("book_edition_edit", kwargs={"book_code": self.work.code, "language": "en"}),
            data={
                "action": "upload_source",
                "book_code": self.work.code,
                "language": "en",
                "title": "Book Test",
                "author_name": "Author Test",
                "publication_year": 2026,
                "source_format": "html",
                "source_file": SimpleUploadedFile(
                    "source.html",
                    b"<html><body>Hello</body></html>",
                    content_type="text/html",
                ),
            },
            follow=False,
        )

        self.assertEqual(upload_response.status_code, 302)
        self.assertEqual(
            upload_response.url,
            reverse("pipeline_html_dashboard", kwargs={"edition_id": self.edition.id}),
        )
        self.assertTrue(raw_path.exists())
        pipeline_state = EditionPipeline.objects.get(edition=self.edition)
        self.assertEqual(pipeline_state.current_stage, "HTML_UPLOADED")
        self.assertEqual(EditionText.objects.get(edition=self.edition).raw_path, str(raw_path))
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
    def test_editorial_autocreate_creates_missing_work_and_edition(self, mock_frontmatter):
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

        self.assertEqual(response.status_code, 302)
        edition = Edition.objects.get(work__code=self.work.code, language__code="en")
        self.assertEqual(
            response.url,
            reverse("pipeline_html_dashboard", kwargs={"edition_id": edition.id}),
        )
        self.assertTrue(Work.objects.filter(code=self.work.code).exists())
        self.assertTrue(Edition.objects.filter(work__code=self.work.code, language__code="en").exists())
        mock_frontmatter.assert_called_once()

    @patch("pipeline.views.kdp_mode.build_frontmatter_files")
    def test_cadastro_work_integrityerror_fallback_keeps_outer_transaction_usable(self, mock_frontmatter):
        from pipeline import views as pipeline_views

        self.edition.delete()
        self.work.delete()

        def fake_insert_work_row_legacy_schema(**kwargs):
            language = Language.objects.get(id=kwargs["language_id"])
            author = Contributor.objects.get(id=kwargs["author_id"])
            work = Work(
                code=kwargs["book_code"],
                title=kwargs["title"],
                original_language=language,
                author=author,
                publisher=kwargs["publisher"],
                year=kwargs["year"],
                is_public_domain=True,
            )
            work.save(force_insert=True)

        upload = SimpleUploadedFile(
            "source.html",
            b"<!doctype html><html><body>Fallback Work</body></html>",
            content_type="text/html",
        )

        with patch.object(
            pipeline_views.Work.objects,
            "get_or_create",
            side_effect=IntegrityError("legacy work create failed"),
        ), patch(
            "pipeline.views._insert_work_row_legacy_schema",
            side_effect=fake_insert_work_row_legacy_schema,
        ):
            response = self.client.post(
                self.cadastro_url,
                data=self._payload("html", upload),
            )

        edition = Edition.objects.get(work__code=self.work.code, language__code="en")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("pipeline_html_dashboard", kwargs={"edition_id": edition.id}),
        )
        self.assertTrue(Work.objects.filter(code=self.work.code).exists())
        mock_frontmatter.assert_called_once()

    @patch("pipeline.views.kdp_mode.build_frontmatter_files")
    def test_cadastro_edition_integrityerror_fallback_keeps_outer_transaction_usable(self, mock_frontmatter):
        from pipeline import views as pipeline_views

        self.edition.delete()

        def fake_insert_edition_row_legacy_schema(**kwargs):
            work = Work.objects.get(id=kwargs["work_id"])
            language = Language.objects.get(id=kwargs["language_id"])
            seal = Seal.objects.get(id=kwargs["seal_id"])
            edition = Edition(
                work=work,
                language=language,
                seal=seal,
                title="Book Test",
                author="Author Test",
                publication_year=2026,
            )
            edition.save(force_insert=True)

        upload = SimpleUploadedFile(
            "source.html",
            b"<!doctype html><html><body>Fallback Edition</body></html>",
            content_type="text/html",
        )

        with patch.object(
            pipeline_views.EditorialEdition.objects,
            "create",
            side_effect=IntegrityError("legacy edition create failed"),
        ), patch(
            "pipeline.views._insert_edition_row_legacy_schema",
            side_effect=fake_insert_edition_row_legacy_schema,
        ):
            response = self.client.post(
                self.cadastro_url,
                data=self._payload("html", upload),
            )

        edition = Edition.objects.get(work__code=self.work.code, language__code="en")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("pipeline_html_dashboard", kwargs={"edition_id": edition.id}),
        )
        self.assertEqual(edition.title, "Book Test")
        mock_frontmatter.assert_called_once()


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
        self.assertContains(response, "Cadastro", status_code=400)
        self.assertContains(response, "Nenhum arquivo chegou ao backend nesta submissao", status_code=400)
        self.assertContains(response, "Campo obrigatorio ausente: source_file.", status_code=400)

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


class MergeTranslatePreviewTests(TestCase):
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
        self.author = Contributor.objects.create(name="Author Merge Preview")
        self.seal = Seal.objects.create(slug="mantaquest-preview", name="MantaQuest Preview")
        self.work = Work.objects.create(
            code="book_0103",
            title="Merge Preview Book",
            original_language=self.language,
            author=self.author,
        )
        self.edition = Edition.objects.create(
            work=self.work,
            language=self.language,
            seal=self.seal,
        )
        self.pipeline_state = EditionPipeline.objects.create(
            edition=self.edition,
            translation_language="en",
        )
        BookEditionTemplate.objects.create(
            book_code=self.work.code,
            language="en",
            title=self.work.title,
            author_name=self.author.name,
            publication_year=2026,
            text_source_mode="html",
        )
        contract_dir = self.temp_root / "gaiden" / "contracts"
        contract_dir.mkdir(parents=True, exist_ok=True)
        (contract_dir / "en_modern_2025.json").write_text(
            json.dumps({"out_dir": "data/translated/book_0001/en_modern_2025"}),
            encoding="utf-8",
        )
        self.runtime_dir = self.temp_root / "data" / "translated" / "book_0103" / "en_modern_2025"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.merged_runtime_path = self.runtime_dir / "merged_en_modern_2025.txt"
        self.merged_runtime_path.write_text("Merged preview content.\n", encoding="utf-8")
        self.preview_url = reverse("preview_merge_translate", kwargs={"edition_id": self.edition.id})
        self.save_url = reverse("save_merge_translate_preview", kwargs={"edition_id": self.edition.id})

    def tearDown(self):
        self.settings_override.disable()
        self.temp_dir.cleanup()

    def test_preview_merge_translate_reads_runtime_out_dir_for_current_book(self):
        response = self.client.get(self.preview_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Merged preview content.")
        self.assertEqual(response.context["md_path"], str(self.merged_runtime_path))

    def test_save_merge_translate_preview_copies_runtime_merge_to_build_dir(self):
        response = self.client.post(self.save_url)

        self.assertEqual(response.status_code, 302)
        saved_path = self.temp_root / "data" / "builds" / self.work.code / "en" / "merge_translate_en.txt"
        self.assertTrue(saved_path.exists())
        self.assertEqual(saved_path.read_text(encoding="utf-8"), "Merged preview content.\n")

    def test_preview_merge_translate_falls_back_to_build_merge_when_runtime_is_missing(self):
        self.merged_runtime_path.unlink()
        build_dir = self.temp_root / "data" / "builds" / self.work.code / "en"
        build_dir.mkdir(parents=True, exist_ok=True)
        build_merge = build_dir / "merge_translate.txt"
        build_merge.write_text("Build merge preview content.\n", encoding="utf-8")

        response = self.client.get(self.preview_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Build merge preview content.")
        self.assertEqual(response.context["md_path"], str(build_merge))


class BookCodeNormalizationTests(TestCase):
    def test_normalize_book_code_input_pads_short_numeric_codes(self):
        self.assertEqual(normalize_book_code_input("book_13"), "book_013")
        self.assertEqual(normalize_book_code_input("13"), "book_013")

    def test_normalize_book_code_input_preserves_existing_width_for_longer_codes(self):
        self.assertEqual(normalize_book_code_input("book_013"), "book_013")
        self.assertEqual(normalize_book_code_input("book_9001"), "book_9001")


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
        self.normalized_dir = self.root / "data" / "normalized"
        self.raw_path = self.raw_dir / f"{self.work.code}_en_raw.html"
        self.clean_path = self.preprod_dir / f"{self.work.code}_en_clean.html"
        self.report_path = self.preprod_dir / f"{self.work.code}_en_report.json"
        self.source_md_path = self.md_dir / f"{self.work.code}_en_source.md"
        self.normalized_md_path = self.md_dir / f"{self.work.code}_en_normalized.md"
        self.canonical_md_path = self.md_dir / f"{self.work.code}_en_canonical.md"
        self.normalized_v2_path = self.normalized_dir / f"{self.work.code}_en_v2.txt"
        self.reupload_url = reverse("pipeline_html_reupload_run", kwargs={"edition_id": self.edition.id})
        self.preprod_url = reverse("pipeline_html_preprod_run", kwargs={"edition_id": self.edition.id})
        self.convert_url = reverse("pipeline_html_convert_run", kwargs={"edition_id": self.edition.id})
        self.dashboard_url = reverse("pipeline_html_dashboard", kwargs={"edition_id": self.edition.id})
        self.normalize_url = reverse("pipeline_normalize_run", kwargs={"edition_id": self.edition.id})

    def tearDown(self):
        shutil.rmtree(self.raw_dir, ignore_errors=True)
        shutil.rmtree(self.preprod_dir, ignore_errors=True)
        shutil.rmtree(self.md_dir, ignore_errors=True)
        if self.normalized_v2_path.exists():
            self.normalized_v2_path.unlink()

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
        self.assertContains(response, "Inicio / Cadastro")
        self.assertContains(response, "Pipeline")
        self.assertContains(response, "Steps")
        self.assertContains(response, "Frontmatter")
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

    def test_preprod_rewrites_numeric_html_chapters_before_md_conversion(self):
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.raw_path.write_text(
            (
                "<html><body>"
                "<p>*** START OF THIS PROJECT GUTENBERG EBOOK ***</p>"
                "<h1>SHADOWS IN THE MOONLIGHT</h1>"
                "<h2>By Robert E. Howard</h2>"
                "<h2>1</h2><p>Opening scene.</p>"
                "<h2>2</h2><p>Second scene.</p>"
                "<p>*** END OF THIS PROJECT GUTENBERG EBOOK ***</p>"
                "</body></html>"
            ),
            encoding="utf-8",
        )
        self.edition.raw_source_path = str(self.raw_path)
        self.edition.save(update_fields=["raw_source_path"])

        response = self.client.post(self.preprod_url)

        self.assertEqual(response.status_code, 302)
        clean_html = self.clean_path.read_text(encoding="utf-8")
        report = json.loads(self.report_path.read_text(encoding="utf-8"))
        self.assertIn("<h2>CHAPTER 1</h2>", clean_html)
        self.assertIn("<h2>CHAPTER 2</h2>", clean_html)
        self.assertEqual(report["chapters_detected"], 2)

    def test_normalize_accepts_raw_html_before_preprod(self):
        self._write_raw_html()

        response = self.client.post(self.normalize_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"{reverse('edition_steps', kwargs={'edition_id': self.edition.id})}?allow_html_to_common=1",
        )
        self.assertTrue(self.normalized_v2_path.exists())
        normalized_text = self.normalized_v2_path.read_text(encoding="utf-8")
        self.assertIn("CHAPTER IV", normalized_text)
        self.assertIn("The quick brown fox.", normalized_text)
        texts = EditionText.objects.get(edition=self.edition)
        self.assertIn("The quick brown fox.", texts.raw_text)
        self.assertEqual(texts.normalized_path, str(self.normalized_v2_path))
        pipeline_state = EditionPipeline.objects.get(edition=self.edition)
        self.assertEqual(pipeline_state.current_stage, "NORMALIZED")

    def test_normalize_refreshes_html_lane_from_source_md_when_it_exists(self):
        self.md_dir.mkdir(parents=True, exist_ok=True)
        self.normalized_dir.mkdir(parents=True, exist_ok=True)
        self.source_md_path.write_text(
            "# SHADOWS IN THE MOONLIGHT\n\n## CHAPTER 1\n\nOpening scene.\n",
            encoding="utf-8",
        )
        self.normalized_v2_path.write_text("stale normalized text\n", encoding="utf-8")
        EditionText.objects.update_or_create(
            edition=self.edition,
            defaults={
                "normalized_text": "stale normalized text\n",
                "normalized_path": str(self.normalized_v2_path),
            },
        )

        response = self.client.post(self.normalize_url)

        self.assertEqual(response.status_code, 302)
        refreshed_text = self.normalized_v2_path.read_text(encoding="utf-8")
        self.assertEqual(refreshed_text, self.source_md_path.read_text(encoding="utf-8"))
        self.assertIn("## CHAPTER 1", refreshed_text)
        texts = EditionText.objects.get(edition=self.edition)
        self.assertEqual(texts.normalized_text, refreshed_text)

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

    def test_convert_preserves_explicit_chapter_markers_from_preprod(self):
        self.preprod_dir.mkdir(parents=True, exist_ok=True)
        self.clean_path.write_text(
            (
                "<html><body>"
                "<h1>SHADOWS IN THE MOONLIGHT</h1>"
                "<h2>By Robert E. Howard</h2>"
                "<h2>CHAPTER 1</h2><p>Opening scene.</p>"
                "<h2>CHAPTER 2</h2><p>Second scene.</p>"
                "</body></html>"
            ),
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
        md_text = self.source_md_path.read_text(encoding="utf-8")
        self.assertIn("## CHAPTER 1", md_text)
        self.assertIn("## CHAPTER 2", md_text)


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
        self.translated_dir = self.root / "data" / "translated" / "book_9001" / "en_modern_2025"
        self.build_dir = self.root / "data" / "builds" / self.book_code / "en"
        self.edition_core_dir = self.root / "data" / "editions" / str(self.edition.id) / "core"

        self.source_md_dir.mkdir(parents=True, exist_ok=True)
        self.source_md_path.write_text(
            (
                "# Book Gate Test\n\n"
                "## Contents\n\n"
                "[I. Chapter One](#chap01){.pginternal}\n"
                "[II. Chapter Two](#chap02){.pginternal}\n\n"
                "----------\n\n"
                "::: chapter\n\n"
                "## CHAPTER I\n\n"
                + ("lorem ipsum " * 1400)
                + "\n\n::: chapter\n\n## CHAPTER II\n\n"
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
        shutil.rmtree(self.root / "data" / "translated" / "book_9001", ignore_errors=True)
        shutil.rmtree(self.root / "data" / "translated" / self.book_code, ignore_errors=True)
        shutil.rmtree(self.build_dir, ignore_errors=True)
        shutil.rmtree(self.edition_core_dir.parent, ignore_errors=True)

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
        self.assertTrue(self.cleaner_dir.exists())
        self.assertTrue((self.cleaner_dir / "clean.txt").exists())
        self.assertTrue((self.cleaner_dir / "heading_cleaner_report.json").exists())

        clean_text = (self.cleaner_dir / "clean.txt").read_text(encoding="utf-8")
        self.assertNotIn("## Contents", clean_text)
        self.assertNotIn(".pginternal", clean_text)
        self.assertNotIn("----------", clean_text)
        self.assertNotIn("::: chapter", clean_text)

    def test_translate_disabled_without_heading_clean(self):
        response = self.client.get(self.steps_url)
        html = response.content.decode("utf-8")

        self.assertFalse(bool(response.context["can_translate"]))
        self.assertRegex(html, r'id="btn_translate"[^>]*disabled')
        self.assertIn("translate_prereq_msg", html)

    def test_translate_enabled_with_heading_clean_and_chunk(self):
        self.client.post(self.heading_url)
        self.client.post(reverse("pipeline_chunk_run", kwargs={"edition_id": self.edition.id}))
        response = self.client.get(self.steps_url)
        html = response.content.decode("utf-8")

        self.assertTrue(bool(response.context["can_translate"]))
        self.assertNotRegex(html, r'id="btn_translate"[^>]*disabled')
        self.assertNotIn("translate_prereq_msg", html)

    def test_heading_cleaner_invalidates_stale_split_and_chunk_rebuilds_from_clean_text(self):
        self.client.post(self.heading_url)
        self.client.post(reverse("pipeline_chunk_run", kwargs={"edition_id": self.edition.id}))
        self.assertTrue(self.split_dir.exists())
        self.assertTrue(any(self.split_dir.glob("*.txt")))

        self.client.post(self.heading_url)
        self.assertFalse(self.split_dir.exists())

        self.client.post(reverse("pipeline_chunk_run", kwargs={"edition_id": self.edition.id}))
        rebuilt_chunks = sorted(self.split_dir.glob("*.txt"))
        self.assertTrue(rebuilt_chunks)
        merged = "\n".join(path.read_text(encoding="utf-8") for path in rebuilt_chunks[:2])
        self.assertNotIn("## Contents", merged)
        self.assertNotIn(".pginternal", merged)

    def test_rechunk_invalidates_stale_downstream_outputs(self):
        self.client.post(self.heading_url)
        self.translated_dir.mkdir(parents=True, exist_ok=True)
        (self.translated_dir / "0001.txt").write_text("stale translate", encoding="utf-8")
        (self.translated_dir / "merged_en_modern_2025.txt").write_text("stale merged", encoding="utf-8")
        (self.translated_dir / "return_aldebaran").mkdir(parents=True, exist_ok=True)
        ((self.translated_dir / "return_aldebaran") / "0001.txt").write_text("stale refine", encoding="utf-8")
        self.build_dir.mkdir(parents=True, exist_ok=True)
        (self.build_dir / "merge_translate.txt").write_text("stale build translate", encoding="utf-8")
        (self.build_dir / "merge_refine.txt").write_text("stale build refine", encoding="utf-8")
        (self.build_dir / "PRE_FLIGHT.json").write_text("{}", encoding="utf-8")
        (self.build_dir / "PRE_FLIGHT.md").write_text("# stale", encoding="utf-8")
        self.edition_core_dir.mkdir(parents=True, exist_ok=True)
        (self.edition_core_dir / "contract_translate_en.json").write_text("{}", encoding="utf-8")
        (self.edition_core_dir / "contract_refine_en.json").write_text("{}", encoding="utf-8")
        (self.edition_core_dir / "refine_input_en").mkdir(parents=True, exist_ok=True)

        response = self.client.post(reverse("pipeline_chunk_run", kwargs={"edition_id": self.edition.id}))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.translated_dir.exists())
        self.assertFalse((self.build_dir / "merge_translate.txt").exists())
        self.assertFalse((self.build_dir / "merge_refine.txt").exists())
        self.assertFalse((self.build_dir / "PRE_FLIGHT.json").exists())
        self.assertFalse((self.build_dir / "PRE_FLIGHT.md").exists())
        self.assertFalse((self.edition_core_dir / "contract_translate_en.json").exists())
        self.assertFalse((self.edition_core_dir / "contract_refine_en.json").exists())
        self.assertFalse((self.edition_core_dir / "refine_input_en").exists())

    def test_runtime_refine_contract_is_hardened_into_prompts(self):
        from gaiden.translate import run_translate_with_contract
        from pipeline.views import _build_runtime_refine_contract

        self.client.post(self.heading_url)
        self.client.post(reverse("pipeline_chunk_run", kwargs={"edition_id": self.edition.id}))
        self.translated_dir.mkdir(parents=True, exist_ok=True)
        (self.translated_dir / "0001.txt").write_text(
            "Holmes spoke plainly.\n\nWatson listened carefully.",
            encoding="utf-8",
        )

        runtime_contract_path, refine_input_dir, out_dir_path = _build_runtime_refine_contract(self.edition, "en")
        payload = json.loads(runtime_contract_path.read_text(encoding="utf-8"))

        self.assertEqual(refine_input_dir, self.edition_core_dir / "refine_input_en")
        self.assertEqual(out_dir_path, self.translated_dir / "return_aldebaran")
        self.assertEqual(payload["refine_profile"], "ingles_neutro")
        self.assertEqual(payload["agent_name"], "Aldebaran")
        self.assertIn("professional literary editor", payload["system_prompt"])
        self.assertIn("Active refine profile: Ingles neutro via agent Aldebaran.", payload["system_prompt"])
        self.assertIn("SURGICAL MICRO-POLISH ONLY", payload["system_prompt"])
        self.assertIn("Do not globally modernize the book.", payload["system_prompt"])
        self.assertIn("Preserve all information, chronology, speakers, dialogue, paragraph structure, and any existing chapter or section headings.", payload["system_prompt"])
        self.assertIn("Do not delete, rename, or renumber existing headings or chapter markers.", payload["system_prompt"])
        self.assertIn("Do not summarize", payload["system_prompt"])
        self.assertIn("Selected profile: Ingles neutro.", payload["user_prompt"])
        self.assertIn("Editing mode: surgical micro-polish only.", payload["user_prompt"])
        self.assertIn("If a line is already strong, leave it unchanged.", payload["user_prompt"])
        self.assertIn("Return only the refined passage", payload["user_prompt"])
        self.assertIn("Do not omit any sentence", payload["user_prompt"])
        self.assertIn("Do not delete or rewrite headings.", payload["user_prompt"])
        self.assertEqual(payload["sanitize_failure_fallback"], "keep_source_chunk")

        with patch("gaiden.translate.get_client", return_value=_FakeOpenAIClient("Here is the refined passage:")):
            run_translate_with_contract(runtime_contract_path)

        refined_chunk = out_dir_path / "0001.txt"
        self.assertTrue(refined_chunk.exists())
        self.assertEqual(
            refined_chunk.read_text(encoding="utf-8"),
            "Holmes spoke plainly.\n\nWatson listened carefully.",
        )
        self.assertTrue((out_dir_path / "merged_return_aldebaran.txt").exists())

    def test_runtime_refine_contract_can_switch_to_ingles_flex_profile(self):
        from pipeline.views import _build_runtime_refine_contract

        self.client.post(self.heading_url)
        self.client.post(reverse("pipeline_chunk_run", kwargs={"edition_id": self.edition.id}))
        self.translated_dir.mkdir(parents=True, exist_ok=True)
        (self.translated_dir / "0001.txt").write_text(
            "Steel rang in the dark while the sorcerer watched.",
            encoding="utf-8",
        )

        runtime_contract_path, _refine_input_dir, _out_dir_path = _build_runtime_refine_contract(
            self.edition,
            "en",
            refine_profile="ingles_flex",
        )
        payload = json.loads(runtime_contract_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["refine_profile"], "ingles_flex")
        self.assertEqual(payload["agent_name"], "Alamaguederaz")
        self.assertIn("flexible adventure English", payload["system_prompt"])
        self.assertIn("sword-and-sorcery flavor", payload["system_prompt"])
        self.assertIn("SURGICAL MICRO-POLISH ONLY", payload["system_prompt"])
        self.assertIn("Selected profile: Ingles flex.", payload["user_prompt"])

    def test_prompt_echo_line_is_stripped_from_generated_chunk_output(self):
        from gaiden.translate import sanitize_generated_chunk_text

        cleaned = sanitize_generated_chunk_text(
            "Please provide the passage from *The People of the Black Circle* that you would like me to modernize.\n\nBody text."
        )

        self.assertEqual(cleaned, "Body text.")

    def test_runtime_translate_contract_raises_max_output_tokens_for_large_chunks(self):
        from pipeline.views import _build_runtime_translate_contract

        self.client.post(self.heading_url)
        self.client.post(reverse("pipeline_chunk_run", kwargs={"edition_id": self.edition.id}))
        large_chunk = self.split_dir / "0003.txt"
        large_chunk.write_text(("A long translated paragraph. " * 320), encoding="utf-8")

        runtime_contract_path, _source_label = _build_runtime_translate_contract(self.edition, "en")
        payload = json.loads(runtime_contract_path.read_text(encoding="utf-8"))

        self.assertGreater(payload["max_output_tokens"], 1200)
        self.assertGreaterEqual(payload["max_output_tokens"], 1800)

    def test_runtime_refine_contract_raises_max_output_tokens_for_large_chunks(self):
        from pipeline.views import _build_runtime_refine_contract

        self.client.post(self.heading_url)
        self.client.post(reverse("pipeline_chunk_run", kwargs={"edition_id": self.edition.id}))
        self.translated_dir.mkdir(parents=True, exist_ok=True)
        (self.translated_dir / "0001.txt").write_text(
            ("A long refined paragraph. " * 320),
            encoding="utf-8",
        )

        runtime_contract_path, _refine_input_dir, _out_dir_path = _build_runtime_refine_contract(
            self.edition,
            "en",
        )
        payload = json.loads(runtime_contract_path.read_text(encoding="utf-8"))

        self.assertGreater(payload["max_output_tokens"], 1200)
        self.assertGreaterEqual(payload["max_output_tokens"], 1800)

    def test_runtime_refine_contract_rejects_truncated_chunk_output(self):
        from gaiden.translate import run_translate_with_contract
        from pipeline.views import _build_runtime_refine_contract

        self.client.post(self.heading_url)
        self.client.post(reverse("pipeline_chunk_run", kwargs={"edition_id": self.edition.id}))
        self.translated_dir.mkdir(parents=True, exist_ok=True)
        (self.translated_dir / "0001.txt").write_text(
            "The barbarian looked back toward the ruins.\n\nThe stars were already paling for dawn.",
            encoding="utf-8",
        )

        runtime_contract_path, _refine_input_dir, out_dir_path = _build_runtime_refine_contract(self.edition, "en")

        with patch("gaiden.translate.get_client", return_value=_FakeOpenAIClient("The barbarian looked back toward the")):
            with self.assertRaisesRegex(RuntimeError, "appears truncated before the chunk boundary"):
                run_translate_with_contract(runtime_contract_path)

        self.assertFalse((out_dir_path / "0001.txt").exists())

    def test_runtime_translate_contract_is_generic_and_not_sherlock_specific(self):
        from pipeline.views import _build_runtime_translate_contract

        self.client.post(self.heading_url)
        self.client.post(reverse("pipeline_chunk_run", kwargs={"edition_id": self.edition.id}))

        runtime_contract_path, source_label = _build_runtime_translate_contract(self.edition, "en")
        payload = json.loads(runtime_contract_path.read_text(encoding="utf-8"))

        self.assertEqual(source_label, "split_01")
        self.assertEqual(payload["chunk_dir"], str(self.split_dir))
        self.assertEqual(payload["out_dir"], str(self.translated_dir))
        self.assertEqual(payload["model"], "gpt-5-chat-latest")
        self.assertNotIn("Sherlock Holmes", payload["system_prompt"])
        self.assertNotIn("Sherlock Holmes", payload["user_prompt"])
        self.assertIn("professional literary editor", payload["system_prompt"])
        self.assertIn("lightly modernized, natural English", payload["user_prompt"])
        self.assertIn("Make only the minimum changes needed for clarity and flow.", payload["user_prompt"])
        self.assertIn("Do not flatten the prose into generic contemporary fantasy", payload["user_prompt"])
        self.assertIn("Return only the final rewritten passage.", payload["user_prompt"])

    def test_merge_refine_stays_blocked_when_refine_outputs_are_partial(self):
        self.client.post(self.heading_url)
        self.split_dir.mkdir(parents=True, exist_ok=True)
        (self.split_dir / "0001.txt").write_text("chunk 1", encoding="utf-8")
        (self.split_dir / "0002.txt").write_text("chunk 2", encoding="utf-8")

        self.translated_dir.mkdir(parents=True, exist_ok=True)
        (self.translated_dir / "0001.txt").write_text("translated 1", encoding="utf-8")
        (self.translated_dir / "0002.txt").write_text("translated 2", encoding="utf-8")
        (self.build_dir / "merge_translate.txt").parent.mkdir(parents=True, exist_ok=True)
        (self.build_dir / "merge_translate.txt").write_text("merged translate", encoding="utf-8")

        refine_dir = self.translated_dir / "return_aldebaran"
        refine_dir.mkdir(parents=True, exist_ok=True)
        (refine_dir / "0001.txt").write_text("partial refine", encoding="utf-8")
        (refine_dir / "merged_return_aldebaran.txt").write_text("partial merged refine", encoding="utf-8")

        response = self.client.get(self.steps_url)
        html = response.content.decode("utf-8")

        self.assertContains(response, "chunks=1/2")
        self.assertRegex(html, r'<button[^>]*disabled[^>]*>\s*Rodar MergeRefine\s*</button>')
        self.assertIn("refine completo com merge correspondente", html)

    def test_steps_show_refine_profile_selector(self):
        response = self.client.get(self.steps_url)

        self.assertContains(response, 'name="refine_profile"')
        self.assertContains(response, "Ingles neutro - Aldebaran")
        self.assertContains(response, "Ingles flex - Alamaguederaz")

    def test_steps_reflect_saved_refine_profile(self):
        EditionPipeline.objects.update_or_create(
            edition=self.edition,
            defaults={"refine_profile": "ingles_flex"},
        )

        response = self.client.get(self.steps_url)

        self.assertContains(response, "5) Refine (Ingles flex)")
        self.assertContains(response, '<option value="ingles_flex" selected>', html=False)

    def test_pipeline01_step_order_is_fixed(self):
        response = self.client.get(self.steps_url)
        html = response.content.decode("utf-8")

        expected = [
            "1) Normalize",
            "2) HeadingCleaner (Mechanical)",
            "3) Split/Chunk",
            "4) Translate (script + JSON)",
            "5) Refine (Ingles neutro)",
            "6) Merge/Finalize",
            "7) Pre-producao (Pre-flight)",
        ]
        positions = [html.find(item) for item in expected]
        self.assertTrue(all(pos >= 0 for pos in positions))
        self.assertEqual(positions, sorted(positions))

    def test_translate_disabled_without_heading_cleaner(self):
        response = self.client.get(self.steps_url)
        html = response.content.decode("utf-8")

        self.assertRegex(html, r'id="btn_translate"[^>]*disabled')

    def test_chunk_post_is_blocked_without_heading_cleaner(self):
        response = self.client.post(reverse("pipeline_chunk_run", kwargs={"edition_id": self.edition.id}))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.steps_url)
        self.assertFalse(self.split_dir.exists())

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
            "2) HeadingCleaner (Mechanical)",
            "3) Split/Chunk",
            "4) Translate (script + JSON)",
            "5) Refine (Ingles neutro)",
            "6) Merge/Finalize",
            "7) Pre-producao (Pre-flight)",
        ]
        positions = [html.find(item) for item in expected]
        self.assertTrue(all(pos >= 0 for pos in positions))
        self.assertEqual(positions, sorted(positions))

    @patch("pipeline.services.preflight.get_client")
    def test_preflight_run_creates_structured_report_after_merge_refine(self, mock_get_client):
        analysis_json = json.dumps(
            {
                "critical": [],
                "medium": ["A passage still sounds syntactically stiff for modern trade reading."],
                "light": ["Minor punctuation noise remains in one local transition."],
                "good": ["Narrative voice stays coherent and commercially readable."],
            }
        )
        mock_get_client.return_value = _FakeOpenAIClient(analysis_json)

        self.split_dir.mkdir(parents=True, exist_ok=True)
        (self.split_dir / "0001.txt").write_text("chunk 1", encoding="utf-8")
        self.translated_dir.mkdir(parents=True, exist_ok=True)
        (self.translated_dir / "0001.txt").write_text("translated 1", encoding="utf-8")
        self.build_dir.mkdir(parents=True, exist_ok=True)
        (self.build_dir / "merge_translate.txt").write_text("merged translate", encoding="utf-8")
        refine_dir = self.translated_dir / "return_aldebaran"
        refine_dir.mkdir(parents=True, exist_ok=True)
        (refine_dir / "0001.txt").write_text("refined 1", encoding="utf-8")
        (refine_dir / "merged_return_aldebaran.txt").write_text("merged refine", encoding="utf-8")
        (self.build_dir / "merge_refine.txt").write_text("build refine", encoding="utf-8")
        translated_clean = self.root / "data" / "translated" / self.book_code / "merge_refine_clean.txt"
        translated_clean.parent.mkdir(parents=True, exist_ok=True)
        translated_clean.write_text(
            "# Chapter I\n\nA clean merged passage for pre-flight review.\n\n# Chapter II\n\nAnother passage.",
            encoding="utf-8",
        )

        response = self.client.post(reverse("pipeline_preflight_run", kwargs={"edition_id": self.edition.id}))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.steps_url)
        preflight_json = self.build_dir / "PRE_FLIGHT.json"
        preflight_md = self.build_dir / "PRE_FLIGHT.md"
        self.assertTrue(preflight_json.exists())
        self.assertTrue(preflight_md.exists())
        self.assertIn("1. PROBLEMAS CRITICOS", preflight_md.read_text(encoding="utf-8"))
        self.assertIn("2. PROBLEMAS MEDIOS", preflight_md.read_text(encoding="utf-8"))
        self.assertIn("4. O QUE ESTA BOM", preflight_md.read_text(encoding="utf-8"))
        self.assertIn("pronto para MD com pequenos ajustes", preflight_md.read_text(encoding="utf-8"))

    @patch("pipeline.services.preflight.REQUEST_TIMEOUT", 0.01)
    @patch("pipeline.services.preflight.get_client")
    def test_preflight_times_out_remote_once_and_falls_back_locally(self, mock_get_client):
        mock_get_client.return_value = _SlowOpenAIClient('{"critical":[],"medium":[],"light":[],"good":[]}', 0.05)

        self.split_dir.mkdir(parents=True, exist_ok=True)
        (self.split_dir / "0001.txt").write_text("chunk 1", encoding="utf-8")
        self.translated_dir.mkdir(parents=True, exist_ok=True)
        (self.translated_dir / "0001.txt").write_text("translated 1", encoding="utf-8")
        self.build_dir.mkdir(parents=True, exist_ok=True)
        (self.build_dir / "merge_translate.txt").write_text("merged translate", encoding="utf-8")
        refine_dir = self.translated_dir / "return_aldebaran"
        refine_dir.mkdir(parents=True, exist_ok=True)
        (refine_dir / "0001.txt").write_text("refined 1", encoding="utf-8")
        (refine_dir / "merged_return_aldebaran.txt").write_text("merged refine", encoding="utf-8")
        (self.build_dir / "merge_refine.txt").write_text("build refine", encoding="utf-8")
        translated_clean = self.root / "data" / "translated" / self.book_code / "merge_refine_clean.txt"
        translated_clean.parent.mkdir(parents=True, exist_ok=True)
        translated_clean.write_text(
            "# Chapter I\n\nA clean merged passage for pre-flight review.\n\n# Chapter II\n\nAnother passage.",
            encoding="utf-8",
        )

        response = self.client.post(reverse("pipeline_preflight_run", kwargs={"edition_id": self.edition.id}))

        self.assertEqual(response.status_code, 302)
        preflight_json = self.build_dir / "PRE_FLIGHT.json"
        preflight_md = self.build_dir / "PRE_FLIGHT.md"
        self.assertTrue(preflight_json.exists())
        self.assertTrue(preflight_md.exists())
        report = json.loads(preflight_json.read_text(encoding="utf-8"))
        joined_light = "\n".join(report["light"])
        self.assertIn("Pre-flight AI fallback acionado", joined_light)

    def test_preflight_step_marks_warning_reports_as_review(self):
        self.split_dir.mkdir(parents=True, exist_ok=True)
        (self.split_dir / "0001.txt").write_text("chunk 1", encoding="utf-8")
        self.translated_dir.mkdir(parents=True, exist_ok=True)
        (self.translated_dir / "0001.txt").write_text("translated 1", encoding="utf-8")
        self.build_dir.mkdir(parents=True, exist_ok=True)
        (self.build_dir / "merge_translate.txt").write_text("merged translate", encoding="utf-8")
        refine_dir = self.translated_dir / "return_aldebaran"
        refine_dir.mkdir(parents=True, exist_ok=True)
        (refine_dir / "0001.txt").write_text("refined 1", encoding="utf-8")
        (refine_dir / "merged_return_aldebaran.txt").write_text("merged refine", encoding="utf-8")
        (self.build_dir / "merge_refine.txt").write_text("build refine", encoding="utf-8")
        translated_clean = self.root / "data" / "translated" / self.book_code / "merge_refine_clean.txt"
        translated_clean.parent.mkdir(parents=True, exist_ok=True)
        translated_clean.write_text("Clean merge for preflight.", encoding="utf-8")
        (self.build_dir / "PRE_FLIGHT.json").write_text(
            json.dumps(
                {
                    "critical": [],
                    "medium": [],
                    "light": ["Pre-flight AI fallback acionado: timeout"],
                    "good": [],
                    "verdict_reason": "Leitura geral aproveitavel.",
                }
            ),
            encoding="utf-8",
        )
        (self.build_dir / "PRE_FLIGHT.md").write_text("# report", encoding="utf-8")

        response = self.client.get(self.steps_url)

        self.assertContains(response, 'class="pipeline-step"')
        self.assertContains(response, "revisar")
        self.assertContains(response, "Relatorio com alertas: 1 leve(s); houve fallback/timeout da IA.")
        self.assertContains(response, "Nao tratar como aprovacao silenciosa.")

    def test_merge_refine_blocks_truncated_refine_chunk(self):
        self.split_dir.mkdir(parents=True, exist_ok=True)
        (self.split_dir / "0001.txt").write_text("source chunk closes cleanly.", encoding="utf-8")
        self.translated_dir.mkdir(parents=True, exist_ok=True)
        (self.translated_dir / "0001.txt").write_text("source chunk closes cleanly.", encoding="utf-8")
        self.build_dir.mkdir(parents=True, exist_ok=True)
        (self.build_dir / "merge_translate.txt").write_text("merged translate", encoding="utf-8")
        refine_dir = self.translated_dir / "return_aldebaran"
        refine_dir.mkdir(parents=True, exist_ok=True)
        (refine_dir / "0001.txt").write_text("source chunk closes with The", encoding="utf-8")
        (refine_dir / "merged_return_aldebaran.txt").write_text("bad merged refine", encoding="utf-8")

        response = self.client.post(
            reverse("pipeline_merge_refine_run", kwargs={"edition_id": self.edition.id}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Step merge_refine failed: MergeRefine blocked: suspicious chunk ending(s) detected.")
        self.assertFalse((self.build_dir / "merge_refine.txt").exists())
        self.assertFalse((self.root / "data" / "translated" / self.book_code / "merge_refine_clean.txt").exists())

    def test_merge_refine_rebuilds_canonical_text_from_chunks(self):
        self.split_dir.mkdir(parents=True, exist_ok=True)
        self.translated_dir.mkdir(parents=True, exist_ok=True)
        self.build_dir.mkdir(parents=True, exist_ok=True)
        (self.build_dir / "merge_translate.txt").write_text("merged translate", encoding="utf-8")

        source_chunk = (
            "## 5 The Black Stallion\n\n"
            "“Watch out!” he said. “I’ve come.”\n"
        )
        (self.split_dir / "0001.txt").write_text(source_chunk, encoding="utf-8")
        (self.translated_dir / "0001.txt").write_text(source_chunk, encoding="utf-8")

        refine_dir = self.translated_dir / "return_aldebaran"
        refine_dir.mkdir(parents=True, exist_ok=True)
        (refine_dir / "0001.txt").write_text(
            'Please provide the passage from *The People of the Black Circle* that you would like me to modernize.\n\n'
            '# The Black Stallion\n\n'
            '"Watch out!" he said. "I\'ve come."\n',
            encoding="utf-8",
        )

        response = self.client.post(
            reverse("pipeline_merge_refine_run", kwargs={"edition_id": self.edition.id}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MergeRefine OK")

        canonical_text = (self.root / "data" / "translated" / self.book_code / "merge_refine_clean.txt").read_text(
            encoding="utf-8"
        )
        self.assertTrue(canonical_text.startswith("## 5 The Black Stallion"))
        self.assertNotIn("Please provide the passage", canonical_text)
        self.assertIn("“Watch out!” he said. “I’ve come.”", canonical_text)

        rewritten_chunk = (refine_dir / "0001.txt").read_text(encoding="utf-8")
        self.assertTrue(rewritten_chunk.startswith("## 5 The Black Stallion"))
        self.assertNotIn("Please provide the passage", rewritten_chunk)
        self.assertIn("“Watch out!” he said. “I’ve come.”", rewritten_chunk)


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
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Salvar e converter imagens")
        self.assertContains(response, "Upload ZIP images")
        self.assertContains(response, "Consolidate internal images")
        self.assertContains(response, "Insert page headlines")
        self.assertContains(response, "Insert image placeholders")
        self.assertContains(response, "Apply images to PRE_EDITION")
        self.assertContains(response, "?allow_html_to_common=1#transformacao-editorial")
        self.assertIn('id="transformacao-editorial"', html)

        section_start = html.index('id="transformacao-editorial"')
        ordered_steps = [
            'data-contract-step="upload_images_files"',
            'data-contract-step="upload_images_zip"',
            'data-contract-step="consolidate_images"',
            'data-contract-step="insert_headlines"',
            'data-contract-step="insert_images"',
            'data-contract-step="apply_images"',
        ]
        step_positions = [html.index(step, section_start) for step in ordered_steps]
        self.assertEqual(step_positions, sorted(step_positions))
        self.assertRegex(
            html,
            re.compile(
                r'data-contract-step="upload_images_files".*?name="action" value="upload_images_files"',
                re.DOTALL,
            ),
        )
        self.assertRegex(
            html,
            re.compile(
                r'data-contract-step="upload_images_zip".*?name="action" value="upload_images_zip"',
                re.DOTALL,
            ),
        )
        self.assertRegex(
            html,
            re.compile(
                r'data-contract-step="insert_images".*?name="action" value="insert_images"',
                re.DOTALL,
            ),
        )

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
        self.assertIn("![](assets/images/ch01_01_01.jpg)", final_md)
        self.assertIn("![](assets/images/ch02_01_02.jpg)", final_md)
        self.assertTrue((self.assets_dir / "ch01_01_01.jpg").exists())
        self.assertTrue((self.assets_dir / "ch02_01_02.jpg").exists())

    def test_insert_images_resets_existing_asset_refs_before_rebuilding_placeholders(self):
        self.pre_edition_path.write_text(
            (
                "# Chapter 01 - The Adventure of the Empty House\n\n"
                "![](assets/images/ch01_01_01.jpg)\n\n"
                "Body of chapter one.\n\n"
                "# Chapter 02 - The Adventure of the Norwood Builder\n\n"
                "![CH02:01](assets/images/ch02_01_02.jpg)\n\n"
                "Body of chapter two.\n"
            ),
            encoding="utf-8",
        )

        response = self.client.post(
            self.steps_url,
            data={"action": "insert_images"},
        )

        self.assertEqual(response.status_code, 302)
        updated_md = self.pre_edition_path.read_text(encoding="utf-8")
        self.assertNotIn("assets/images/ch01_01_01.jpg", updated_md)
        self.assertNotIn("assets/images/ch02_01_02.jpg", updated_md)
        self.assertIn("{{IMAGE:CH01:01}}", updated_md)
        self.assertIn("{{IMAGE:CH02:01}}", updated_md)


class MdTransformSourceHeadingContractTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.temp_web = self.temp_root / "web"
        self.temp_web.mkdir(parents=True, exist_ok=True)
        self.settings_override = override_settings(BASE_DIR=self.temp_web)
        self.settings_override.enable()

    def tearDown(self):
        self.settings_override.disable()
        self.temp_dir.cleanup()

    def test_txt_to_md_uses_source_md_story_titles_and_ignores_false_chapters(self):
        from pipeline.services import md_transform

        source_dir = self.temp_root / "data" / "md" / "book_0200"
        source_dir.mkdir(parents=True, exist_ok=True)
        source_md = source_dir / "book_0200_en_source.md"
        source_md.write_text(
            (
                "# THE TEST CASEBOOK\n\n"
                "### I\n\n"
                "### THE ADVENTURE OF THE FIRST CASE\n\n"
                "Opening paragraph one. It starts the first case.\n\n"
                "### II\n\n"
                "### THE PROBLEM OF THE SECOND CASE\n\n"
                "Opening paragraph two. It starts the second case.\n"
            ),
            encoding="utf-8",
        )

        txt_path = self.temp_root / "merge_refine_clean.txt"
        txt_path.write_text(
            (
                "THE TEST CASEBOOK\n\n"
                "BY TEST AUTHOR\n\n"
                "First published 1927\n\n"
                "CONTENTS\n\n"
                "Opening paragraph one. It starts the first case.\n\n"
                "More of the first case.\n\n"
                "Opening paragraph two. It starts the second case.\n\n"
                "More of the second case.\n\n"
                "| Designer of Agricultural Machinery. |\n"
            ),
            encoding="utf-8",
        )

        out_path = self.temp_root / "BOOK.PRE_EDITION.md"
        md_transform.pre_edition_txt_to_md(
            txt_path,
            out_path,
            md_transform.PreEditionConfig(
                title="The Test Casebook",
                book_code="book_0200",
                language="en",
            ),
        )

        output = out_path.read_text(encoding="utf-8")
        self.assertIn("# Chapter 01 - The Adventure of the First Case", output)
        self.assertIn("# Chapter 02 - The Problem of the Second Case", output)
        self.assertNotIn("# Chapter 01 - First published 1927", output)
        self.assertNotIn("# Chapter 03 - | Designer of Agricultural Machinery. |", output)

    def test_txt_to_md_can_segment_by_split_and_refine_chunks_when_literal_markers_do_not_match(self):
        from pipeline.services import md_transform

        split_dir = self.temp_root / "data" / "chunks" / "book_0201" / "split_01"
        split_dir.mkdir(parents=True, exist_ok=True)
        (split_dir / "0001.txt").write_text("PREFACE\n\nOpening prefatory matter.", encoding="utf-8")
        (split_dir / "0002.txt").write_text(
            "### THE ADVENTURE OF THE FIRST CASE\n\nOriginal opening one.",
            encoding="utf-8",
        )
        (split_dir / "0003.txt").write_text("Continuation one.", encoding="utf-8")
        (split_dir / "0004.txt").write_text(
            "### THE PROBLEM OF THE SECOND CASE\n\nOriginal opening two.",
            encoding="utf-8",
        )
        (split_dir / "0005.txt").write_text("Continuation two.", encoding="utf-8")

        refined_dir = self.temp_root / "data" / "translated" / "book_0201" / "en_modern_2025" / "return_aldebaran"
        refined_dir.mkdir(parents=True, exist_ok=True)
        (refined_dir / "0001.txt").write_text("Preface kept in refined text.", encoding="utf-8")
        (refined_dir / "0002.txt").write_text("Rewritten opening for case one.", encoding="utf-8")
        (refined_dir / "0003.txt").write_text("Continuation for case one.", encoding="utf-8")
        (refined_dir / "0004.txt").write_text("Rewritten opening for case two.", encoding="utf-8")
        (refined_dir / "0005.txt").write_text("Continuation for case two.", encoding="utf-8")

        source_dir = self.temp_root / "data" / "md" / "book_0201"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "book_0201_en_source.md").write_text(
            (
                "# TEST BOOK\n\n"
                "### I\n\n"
                "### THE ADVENTURE OF THE FIRST CASE\n\n"
                "Original opening one.\n\n"
                "### II\n\n"
                "### THE PROBLEM OF THE SECOND CASE\n\n"
                "Original opening two.\n"
            ),
            encoding="utf-8",
        )

        txt_path = self.temp_root / "merge_refine.txt"
        txt_path.write_text(
            (
                "Preface kept in refined text.\n\n"
                "Rewritten opening for case one.\n\n"
                "Continuation for case one.\n\n"
                "Rewritten opening for case two.\n\n"
                "Continuation for case two.\n"
            ),
            encoding="utf-8",
        )

        out_path = self.temp_root / "BOOK.PRE_EDITION.md"
        md_transform.pre_edition_txt_to_md(
            txt_path,
            out_path,
            md_transform.PreEditionConfig(
                title="Test Book",
                book_code="book_0201",
                language="en",
            ),
        )

        output = out_path.read_text(encoding="utf-8")
        self.assertIn("Preface kept in refined text.", output)
        self.assertIn("# Chapter 01 - The Adventure of the First Case", output)
        self.assertIn("Rewritten opening for case one.", output)
        self.assertIn("Continuation for case one.", output)
        self.assertIn("# Chapter 02 - The Problem of the Second Case", output)
        self.assertIn("Rewritten opening for case two.", output)
        first_idx = output.index("# Chapter 01 - The Adventure of the First Case")
        second_idx = output.index("# Chapter 02 - The Problem of the Second Case")
        self.assertLess(output.index("Rewritten opening for case one."), second_idx)
        self.assertGreater(output.index("Rewritten opening for case two."), second_idx)

    def test_heading_contract_prefers_split_map_over_source_marker_heuristics(self):
        from pipeline.services import md_transform

        split_dir = self.temp_root / "data" / "chunks" / "book_0202" / "split_01"
        split_dir.mkdir(parents=True, exist_ok=True)
        (split_dir / "0001.txt").write_text("PREFACE\n\nFront matter.", encoding="utf-8")
        (split_dir / "0002.txt").write_text(
            "### THE ADVENTURE OF THE FIRST CASE\n\nOriginal opening one.",
            encoding="utf-8",
        )
        (split_dir / "0003.txt").write_text("Continuation one.", encoding="utf-8")
        (split_dir / "0004.txt").write_text(
            "### THE PROBLEM OF THE SECOND CASE\n\nOriginal opening two.",
            encoding="utf-8",
        )

        source_dir = self.temp_root / "data" / "md" / "book_0202"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "book_0202_en_source.md").write_text(
            (
                "# TEST BOOK\n\n"
                "### I\n\n"
                "### THE ADVENTURE OF THE FIRST CASE\n\n"
                "Original opening one.\n\n"
                "### II\n\n"
                "### THE PROBLEM OF THE SECOND CASE\n\n"
                "Original opening two.\n"
            ),
            encoding="utf-8",
        )

        txt_path = self.temp_root / "merge_refine.txt"
        txt_path.write_text(
            (
                "Front matter.\n\n"
                "Rewritten opening for case one.\n\n"
                "Continuation for case one.\n\n"
                "Rewritten opening for case two.\n"
            ),
            encoding="utf-8",
        )

        contract = md_transform._resolve_heading_contract(
            txt_path,
            md_transform.PreEditionConfig(
                title="Test Book",
                book_code="book_0202",
                language="en",
            ),
        )

        self.assertIsNotNone(contract)
        self.assertEqual(contract.source, "split_01")
        self.assertEqual(
            contract.titles,
            [
                "The Adventure of the First Case",
                "The Problem of the Second Case",
            ],
        )

    def test_heading_contract_maps_numeric_chapters_to_expected_editorial_titles(self):
        from pipeline.services import md_transform

        split_dir = self.temp_root / "data" / "chunks" / "book_0012" / "split_01"
        split_dir.mkdir(parents=True, exist_ok=True)
        (split_dir / "0001.txt").write_text("# TEST BOOK", encoding="utf-8")
        (split_dir / "0002.txt").write_text("## 1\n\nOpening one.", encoding="utf-8")
        (split_dir / "0003.txt").write_text("Continuation one.", encoding="utf-8")
        (split_dir / "0004.txt").write_text("## 2\n\nOpening two.", encoding="utf-8")
        (split_dir / "0005.txt").write_text("## 3\n\nOpening three.", encoding="utf-8")
        (split_dir / "0006.txt").write_text("## 4\n\nOpening four.", encoding="utf-8")

        contract = md_transform._resolve_heading_contract(
            self.temp_root / "merge_refine.txt",
            md_transform.PreEditionConfig(
                title="Shadows in the Moonlight",
                book_code="book_012",
                language="en",
            ),
        )

        self.assertIsNotNone(contract)
        self.assertEqual(contract.source, "split_01")
        self.assertEqual(
            contract.titles,
            [
                "Escape from the Black Coast",
                "The Isle in the Moonlight",
                "The Statues That Walk",
                "The Night of the Iron Shadows",
            ],
        )

    def test_pre_edition_md_uses_editorial_titles_for_numeric_chapters(self):
        from pipeline.services import md_transform

        split_dir = self.temp_root / "data" / "chunks" / "book_0012" / "split_01"
        split_dir.mkdir(parents=True, exist_ok=True)
        (split_dir / "0001.txt").write_text("# SHADOWS IN THE MOONLIGHT", encoding="utf-8")
        (split_dir / "0002.txt").write_text("## 1\n\nOpening one.", encoding="utf-8")
        (split_dir / "0003.txt").write_text("Body one.", encoding="utf-8")
        (split_dir / "0004.txt").write_text("## 2\n\nOpening two.", encoding="utf-8")
        (split_dir / "0005.txt").write_text("## 3\n\nOpening three.", encoding="utf-8")
        (split_dir / "0006.txt").write_text("## 4\n\nOpening four.", encoding="utf-8")

        refined_dir = self.temp_root / "data" / "translated" / "book_0012" / "en_modern_2025" / "return_aldebaran"
        refined_dir.mkdir(parents=True, exist_ok=True)
        (refined_dir / "0001.txt").write_text("Front matter.", encoding="utf-8")
        (refined_dir / "0002.txt").write_text("Rewritten opening one.", encoding="utf-8")
        (refined_dir / "0003.txt").write_text("Continuation one.", encoding="utf-8")
        (refined_dir / "0004.txt").write_text("Rewritten opening two.", encoding="utf-8")
        (refined_dir / "0005.txt").write_text("Rewritten opening three.", encoding="utf-8")
        (refined_dir / "0006.txt").write_text("Rewritten opening four.", encoding="utf-8")

        source_dir = self.temp_root / "data" / "md" / "book_012"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "book_012_en_source.md").write_text(
            (
                "# SHADOWS IN THE MOONLIGHT\n\n"
                "## CHAPTER 1\n\nOpening one.\n\n"
                "## CHAPTER 2\n\nOpening two.\n\n"
                "## CHAPTER 3\n\nOpening three.\n\n"
                "## CHAPTER 4\n\nOpening four.\n"
            ),
            encoding="utf-8",
        )

        txt_path = self.temp_root / "merge_refine.txt"
        txt_path.write_text(
            (
                "Front matter.\n\n"
                "Rewritten opening one.\n\n"
                "Continuation one.\n\n"
                "Rewritten opening two.\n\n"
                "Rewritten opening three.\n\n"
                "Rewritten opening four.\n"
            ),
            encoding="utf-8",
        )

        out_path = self.temp_root / "BOOK.PRE_EDITION.md"
        md_transform.pre_edition_txt_to_md(
            txt_path,
            out_path,
            md_transform.PreEditionConfig(
                title="Shadows in the Moonlight",
                book_code="book_012",
                language="en",
            ),
        )

        output = out_path.read_text(encoding="utf-8")
        self.assertIn("# Chapter 01 - Escape from the Black Coast", output)
        self.assertIn("# Chapter 02 - The Isle in the Moonlight", output)
        self.assertIn("# Chapter 03 - The Statues That Walk", output)
        self.assertIn("# Chapter 04 - The Night of the Iron Shadows", output)

    def test_pre_edition_md_uses_editorial_titles_for_book_014(self):
        from pipeline.services import md_transform

        split_dir = self.temp_root / "data" / "chunks" / "book_0014" / "split_01"
        split_dir.mkdir(parents=True, exist_ok=True)
        (split_dir / "0001.txt").write_text("# JEWELS OF GWAHLUR", encoding="utf-8")
        (split_dir / "0002.txt").write_text("## By Robert E. Howard", encoding="utf-8")
        (split_dir / "0003.txt").write_text("## 1\n\nOpening one.", encoding="utf-8")
        (split_dir / "0004.txt").write_text("Body one.", encoding="utf-8")
        (split_dir / "0005.txt").write_text("## 2\n\nOpening two.", encoding="utf-8")
        (split_dir / "0006.txt").write_text("Body two.", encoding="utf-8")
        (split_dir / "0007.txt").write_text("## 3\n\nOpening three.", encoding="utf-8")
        (split_dir / "0008.txt").write_text("Body three.", encoding="utf-8")
        (split_dir / "0009.txt").write_text("## 4\n\nOpening four.", encoding="utf-8")
        (split_dir / "0010.txt").write_text("Body four.", encoding="utf-8")
        (split_dir / "0011.txt").write_text("## 5\n\nOpening five.", encoding="utf-8")
        (split_dir / "0012.txt").write_text("Body five.", encoding="utf-8")

        source_dir = self.temp_root / "data" / "md" / "book_014"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "book_014_en_source.md").write_text(
            (
                "# JEWELS OF GWAHLUR\n\n"
                "## 1\n\nOpening one.\n\n"
                "## 2\n\nOpening two.\n\n"
                "## 3\n\nOpening three.\n\n"
                "## 4\n\nOpening four.\n\n"
                "## 5\n\nOpening five.\n"
            ),
            encoding="utf-8",
        )

        txt_path = self.temp_root / "merge_refine.txt"
        txt_path.write_text(
            (
                "Opening one.\n\n"
                "Body one.\n\n"
                "Opening two.\n\n"
                "Body two.\n\n"
                "Opening three.\n\n"
                "Body three.\n\n"
                "Opening four.\n\n"
                "Body four.\n\n"
                "Opening five.\n\n"
                "Body five.\n"
            ),
            encoding="utf-8",
        )

        out_path = self.temp_root / "BOOK.PRE_EDITION.md"
        md_transform.pre_edition_txt_to_md(
            txt_path,
            out_path,
            md_transform.PreEditionConfig(
                title="Jewels of Gwahlur",
                book_code="book_014",
                language="en",
            ),
        )

        output = out_path.read_text(encoding="utf-8")
        self.assertIn("# Chapter 01 - The Shrine of Gwahlur", output)
        self.assertIn("# Chapter 02 - Muriela the Queen", output)
        self.assertIn("# Chapter 03 - The Hidden Temple", output)
        self.assertIn("# Chapter 04 - The God That Walks", output)
        self.assertIn("# Chapter 05 - The Jewels of Gwahlur", output)

    def test_pre_edition_md_rewrites_existing_md_headings_for_book_014(self):
        from pipeline.services import md_transform

        split_dir = self.temp_root / "data" / "chunks" / "book_0014" / "split_01"
        split_dir.mkdir(parents=True, exist_ok=True)
        (split_dir / "0001.txt").write_text("# JEWELS OF GWAHLUR", encoding="utf-8")
        (split_dir / "0002.txt").write_text("## By Robert E. Howard", encoding="utf-8")
        (split_dir / "0003.txt").write_text("## 1 The Shrine of Gwahlur", encoding="utf-8")
        (split_dir / "0004.txt").write_text("## 2 Muriela the Queen", encoding="utf-8")
        (split_dir / "0005.txt").write_text("## 3 The Hidden Temple", encoding="utf-8")
        (split_dir / "0006.txt").write_text("## 4 The God That Walks", encoding="utf-8")
        (split_dir / "0007.txt").write_text("## 5 The Jewels of Gwahlur", encoding="utf-8")

        txt_path = self.temp_root / "merge_refine.txt"
        txt_path.write_text(
            (
                "# JEWELS OF GWAHLUR\n\n"
                "## By Robert E. Howard\n\n"
                "## 1 The Shrine of Gwahlur\n\n"
                "Opening one.\n\n"
                "## 2 Muriela the Queen\n\n"
                "Opening two.\n\n"
                "## 3 The Hidden Temple\n\n"
                "Opening three.\n\n"
                "## 4 The God That Walks\n\n"
                "Opening four.\n\n"
                "## 5 The Jewels of Gwahlur\n\n"
                "Opening five.\n"
            ),
            encoding="utf-8",
        )

        out_path = self.temp_root / "BOOK.PRE_EDITION.md"
        md_transform.pre_edition_txt_to_md(
            txt_path,
            out_path,
            md_transform.PreEditionConfig(
                title="Jewels of Gwahlur",
                book_code="book_014",
                language="en",
            ),
        )

        output = out_path.read_text(encoding="utf-8")
        self.assertIn("# Chapter 01 - The Shrine of Gwahlur", output)
        self.assertIn("# Chapter 05 - The Jewels of Gwahlur", output)
        self.assertNotIn("## 1 The Shrine of Gwahlur", output)
        self.assertNotIn("## 5 The Jewels of Gwahlur", output)

    def test_heading_contract_fails_when_expected_titles_do_not_match(self):
        from pipeline.services import md_transform

        split_dir = self.temp_root / "data" / "chunks" / "book_0203" / "split_01"
        split_dir.mkdir(parents=True, exist_ok=True)
        (split_dir / "0001.txt").write_text(
            "### THE ADVENTURE OF THE WRONG CASE\n\nOriginal opening one.",
            encoding="utf-8",
        )
        (split_dir / "0002.txt").write_text(
            "### THE PROBLEM OF THE SECOND CASE\n\nOriginal opening two.",
            encoding="utf-8",
        )

        with patch.dict(
            md_transform.EXPECTED_CHAPTER_TITLES,
            {("book_0203", "en"): ["The Adventure of the First Case", "The Problem of the Second Case"]},
            clear=False,
        ):
            with self.assertRaises(RuntimeError) as exc:
                md_transform._resolve_heading_contract(
                    self.temp_root / "merge_refine.txt",
                    md_transform.PreEditionConfig(
                        title="Test Book",
                        book_code="book_0203",
                        language="en",
                    ),
                )

        self.assertIn("Chapter title contract failed", str(exc.exception))
        self.assertIn("expected='The Adventure of the First Case'", str(exc.exception))
        self.assertIn("actual='The Adventure of the Wrong Case'", str(exc.exception))


class KdpMarkerCleanupContractTests(TestCase):
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
        self.author = Contributor.objects.create(name="Author Marker Contract")
        self.seal = Seal.objects.create(slug="mantaquest-marker", name="MantaQuest Marker")
        self.work = Work.objects.create(
            code="book_0102",
            title="Marker Contract Book",
            original_language=self.language,
            author=self.author,
        )
        self.edition = Edition.objects.create(
            work=self.work,
            language=self.language,
            seal=self.seal,
        )

        self.build_dir = Path("data") / "builds" / self.work.code / "en"
        self.pre_edition_path = self.build_dir / "BOOK.PRE_EDITION.md"
        self.build_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(Path("data") / "builds" / self.work.code, ignore_errors=True)
        shutil.rmtree(Path("data") / "translated" / self.work.code, ignore_errors=True)
        self.settings_override.disable()
        self.temp_dir.cleanup()

    def test_build_kdp_cleans_leaked_body_markers_and_writes_report(self):
        from editorial import kdp_mode

        self.pre_edition_path.write_text(
            (
                "# Chapter 01 - The Sign\n\n"
                "CH01:01 The moor was dark and silent.\n\n"
                "![](assets/images/ch01_01_sign.jpg)\n\n"
                "# Chapter 02 - The Return\n\n"
                "CHAPTER02:01\n\n"
                "CAP03:01 The door was locked.\n\n"
                "![CH02:01](assets/images/ch02_01_return.jpg)\n"
            ),
            encoding="utf-8",
        )

        merged_path = kdp_mode.build_merged_kdp_source(self.edition)
        merged_text = merged_path.read_text(encoding="utf-8")
        report_path = self.build_dir / "BOOK.MARKER_CLEANUP_REPORT.json"

        self.assertNotIn("CH01:01", merged_text)
        self.assertNotIn("CHAPTER02:01", merged_text)
        self.assertNotIn("CAP03:01", merged_text)
        self.assertNotIn("![CH02:01]", merged_text)
        self.assertIn("![](assets/images/ch02_01_return.jpg)", merged_text)
        self.assertIn("The moor was dark and silent.", merged_text)
        self.assertIn("The door was locked.", merged_text)
        self.assertTrue(report_path.exists())
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(len(report), 4)
        self.assertEqual(report[-1]["kind"], "image_alt")

    def test_build_kdp_raises_manual_review_when_marker_leaks_into_heading(self):
        from editorial import kdp_mode

        self.pre_edition_path.write_text(
            "# Chapter 01 - CH01:01 Broken heading\n\nBody text.\n",
            encoding="utf-8",
        )

        with self.assertRaises(RuntimeError):
            kdp_mode.build_merged_kdp_source(self.edition)

    def test_build_kdp_strips_legacy_title_page_and_contents_and_labels_preface(self):
        from editorial import kdp_mode

        self.pre_edition_path.write_text(
            (
                "::: center\n"
                "# Marker Contract Book\n\n"
                "## Illustrated Edition\n"
                ":::\n\n"
                "MARKER CONTRACT BOOK\n\n"
                "BY AUTHOR MARKER CONTRACT\n\n"
                "LONDON\n"
                "JOHN MURRAY\n\n"
                "First published 1927\n\n"
                "This is the real prefatory paragraph that should remain in the book and not be swallowed by the generated frontmatter.\n\n"
                "CONTENTS\n\n"
                "I First Case\n"
                "II Second Case\n\n"
                "# Chapter 01 - First Case\n\n"
                "Body text.\n"
            ),
            encoding="utf-8",
        )

        merged_path = kdp_mode.build_merged_kdp_source(self.edition)
        merged_text = merged_path.read_text(encoding="utf-8")

        self.assertIn("# Adapted Preface", merged_text)
        self.assertIn("This is the real prefatory paragraph", merged_text)
        self.assertIn("## Chapter 01 - First Case", merged_text)
        self.assertNotIn("MARKER CONTRACT BOOK", merged_text)
        self.assertNotIn("First published 1927", merged_text)
        self.assertNotIn("\nCONTENTS\n", merged_text)

    def test_build_kdp_keeps_numeric_chapter_heading_and_moves_visual_title_below_image(self):
        from editorial import kdp_mode

        self.pre_edition_path.write_text(
            (
                "## 1 Death Strikes a King\n"
                "![](assets/images/ch01_01.jpg)\n\n"
                "The king of Vendhya was dying.\n"
            ),
            encoding="utf-8",
        )

        merged_path = kdp_mode.build_merged_kdp_source(self.edition)
        merged_text = merged_path.read_text(encoding="utf-8")

        self.assertIn("## Chapter 01 - Death Strikes a King", merged_text)
        self.assertIn("**Chapter 01 - Death Strikes a King**", merged_text)
        chapter_idx = merged_text.index("## Chapter 01 - Death Strikes a King")
        image_idx = merged_text.index("![](assets/images/ch01_01.jpg)")
        visual_idx = merged_text.index("**Chapter 01 - Death Strikes a King**")
        body_idx = merged_text.index("The king of Vendhya was dying.")
        self.assertLess(chapter_idx, image_idx)
        self.assertLess(image_idx, visual_idx)
        self.assertLess(visual_idx, body_idx)

    def test_build_kdp_does_not_duplicate_markdown_visual_chapter_title_on_rebuild(self):
        from editorial import kdp_mode

        self.pre_edition_path.write_text(
            (
                "## 1 Death Strikes a King\n"
                "![](assets/images/ch01_01.jpg)\n\n"
                "The king of Vendhya was dying.\n"
            ),
            encoding="utf-8",
        )

        first = kdp_mode.build_merged_kdp_source(self.edition).read_text(encoding="utf-8")
        second = kdp_mode.build_merged_kdp_source(self.edition).read_text(encoding="utf-8")

        self.assertEqual(first.count("**Chapter 01 - Death Strikes a King**"), 1)
        self.assertEqual(second.count("**Chapter 01 - Death Strikes a King**"), 1)


class MdApproveImagesContractTests(TestCase):
    def setUp(self):
        self.language = Language.objects.create(
            code="en",
            name="English",
            native_name="English",
            is_active=True,
        )
        self.author = Contributor.objects.create(name="Author Approval Contract")
        self.seal = Seal.objects.create(slug="mantaquest-approve", name="MantaQuest Approve")
        self.work = Work.objects.create(
            code="book_0103",
            title="Approval Contract Book",
            original_language=self.language,
            author=self.author,
        )
        self.edition = Edition.objects.create(
            work=self.work,
            language=self.language,
            seal=self.seal,
        )
        self.build_dir = Path("data") / "builds" / self.work.code / "en"
        self.build_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(Path("data") / "builds" / self.work.code, ignore_errors=True)

    def test_approve_md_prefers_pre_edition_when_it_has_images(self):
        from pipeline.services import md_quality, paths

        (self.build_dir / "BOOK.QA.md").write_text(
            "# Chapter 01 - Case One\n\nBody without images.\n",
            encoding="utf-8",
        )
        (self.build_dir / "BOOK.PRE_EDITION.md").write_text(
            "# Chapter 01 - Case One\n![](assets/images/ch01_01_01.jpg)\n\nBody with image.\n",
            encoding="utf-8",
        )

        result = md_quality.approve_md_final(self.edition)
        final_text = paths.final_md_path(self.edition).read_text(encoding="utf-8")

        self.assertEqual(Path(result["source"]).resolve(), (self.build_dir / "BOOK.PRE_EDITION.md").resolve())
        self.assertIn("![](assets/images/ch01_01_01.jpg)", final_text)
