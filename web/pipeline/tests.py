import io
import json
import os
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
from django.utils import timezone

from editorial.models import Contributor, Edition, EditionBuild, EditionPipeline, EditionText, Language, Seal, Work
from pipeline.forms import normalize_book_code_input
from pipeline.models import BookEditionTemplate, CORE_BLOCK_KEY, CORE_ISOLATION_LANGUAGES, SYSTEM_BLOCKS


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


class _SequentialResponsesAPI:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item

        class _Resp:
            pass

        resp = _Resp()
        resp.output_text = item
        return resp


class _SequentialOpenAIClient:
    def __init__(self, responses):
        self.responses = _SequentialResponsesAPI(responses)


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


class ItalianSupportTests(TestCase):
    def test_italian_refine_contract_and_frontmatter_helpers(self):
        from editorial.frontmatter import frontmatter_headings, language_display
        from gaiden_portal.utils import country_for_language, get_section_template_for_language
        from pipeline.views import (
            _default_refine_profile_for_language,
            _resolve_refine_output_dir,
        )

        headings = frontmatter_headings("it")
        self.assertEqual(language_display("it"), "Italiano")
        self.assertEqual(headings["preface"], "Prefazione")
        self.assertEqual(headings["introduction"], "Introduzione")
        self.assertEqual(headings["about_contributor"], "Sull'autore")
        self.assertEqual(country_for_language("it", "fallback"), "Brasile")
        self.assertEqual(get_section_template_for_language("about_edition", "it"), "pipeline/about_edition_it.md.j2")
        self.assertEqual(_default_refine_profile_for_language("it"), "italiano_neutro")
        self.assertEqual(
            _resolve_refine_output_dir(Path("data/translated/book_0002/it_2026"), target_language="it"),
            Path("data/translated/book_0002/it_2026/return_aldebaran"),
        )
        self.assertEqual(
            _resolve_refine_output_dir(
                Path("data/builds/book_0002/it/split_by_chapter/parts"),
                target_language="it",
            ),
            Path("data/builds/book_0002/it/split_by_chapter/return_aldebaran"),
        )

    def test_italian_template_placeholder_context_uses_italiano(self):
        template = BookEditionTemplate(
            book_code="book_9999",
            language="it",
            title="Titolo",
            author_name="Autore",
            publication_year=2026,
        )
        self.assertEqual(template.get_placeholder_context()["language"], "Italiano")


class FrenchRefineRoutingTests(TestCase):
    def test_french_refine_defaults_to_coulhon(self):
        from pipeline.views import _default_refine_profile_for_language, _refine_profile_config, _refine_profile_keys_for_language

        self.assertEqual(_default_refine_profile_for_language("fr"), "fr_coulhon")
        self.assertEqual(_refine_profile_keys_for_language("fr"), ("fr_coulhon", "fr_colhoun"))
        self.assertEqual(_refine_profile_config("fr_coulhon")["agent_name"], "Le Grand Coulhon")
        self.assertEqual(_refine_profile_config("fr_colhoun")["agent_name"], "Le_Gran_Colhoun")

    def test_french_refine_output_dir_uses_agent_slug(self):
        from pipeline.views import _resolve_refine_output_dir

        self.assertEqual(
            _resolve_refine_output_dir(
                Path("data/translated/book_0002/fr_2026"),
                refine_profile="fr_coulhon",
                target_language="fr",
            ),
            Path("data/translated/book_0002/fr_2026/return_le_grand_coulhon"),
        )


class FrenchPolishRoutingTests(TestCase):
    def test_french_polish_defaults_to_frances_polidor(self):
        from pipeline.views import _default_polish_agent_for_language, _polish_agent_options_for_language, _normalize_agent_name

        self.assertEqual(_default_polish_agent_for_language("fr"), "Francês_Polidor")
        self.assertEqual(_polish_agent_options_for_language("fr"), ("Francês_Polidor",))
        self.assertEqual(_normalize_agent_name("Francês_Polidor"), "Francês_Polidor")


class TranslateAgentRoutingTests(TestCase):
    def test_french_translate_uses_internal_agent_id(self):
        from pipeline.views import _translate_agent_name

        self.assertEqual(_translate_agent_name("fr"), "translate_fr_2026")
        self.assertEqual(_translate_agent_name("fr", "LE_GRAN_COLHOUN"), "translate_fr_2026")
        self.assertEqual(_translate_agent_name("ptbr"), "translate_pt_br_2026")
        self.assertEqual(_translate_agent_name("pt-br"), "translate_pt_br_2026")

    def test_agent_translate_default_resolves_french_agent(self):
        from gaiden.tools.agent_translate_default import resolve_agent_for_target

        self.assertEqual(resolve_agent_for_target(suffix="fr"), "translate_fr_2026")
        self.assertEqual(resolve_agent_for_target(suffix="ptbr"), "translate_pt_br_2026")
        self.assertEqual(
            resolve_agent_for_target(suffix="fr", requested_agent="LE_GRAN_COLHOUN"),
            "translate_fr_2026",
        )


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

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pagina 01")
        self.assertContains(response, "Cadastro do livro")
        self.assertContains(response, "Livros existentes")
        self.assertContains(response, "Salvar")
        self.assertNotContains(response, "Enviar arquivo")
        self.assertContains(response, "Bloco 01 · Entrada")
        self.assertContains(response, "Bloco 02 · Core")
        self.assertContains(response, "Bloco 03 · Editorial")
        self.assertContains(response, "Bloco 04 · Finalizacao")
        self.assertContains(response, "Obra autoral")
        self.assertContains(response, "Obra de dominio publico")

    def test_root_shows_project_type_choice_before_cadastro(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Criar novo projeto editorial")
        self.assertContains(response, "Book")
        self.assertContains(response, "Collection")

    def test_cadastro_shows_existing_books_in_single_selector(self):
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
        self.assertContains(response, "Livros existentes")
        self.assertContains(response, "01 - book_0001 [en] - Continue Book")
        self.assertContains(response, 'name="book"', html=False)
        self.assertNotContains(response, reverse("edition_steps", kwargs={"edition_id": edition.id}))

    def test_cadastro_hides_existing_books_table_until_book_is_selected(self):
        work = Work.objects.create(
            code="book_0002",
            title="Hidden Table Book",
            original_language=self.language,
            author=self.author,
        )
        Edition.objects.create(
            work=work,
            language=self.language,
            seal=self.seal,
            title="Hidden Table Book",
        )

        response = self.client.get(self.cadastro_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selecione um livro e clique em <strong>Estado do livro</strong> para exibir o relatorio.", html=True)
        self.assertNotContains(response, "<th>Book code</th>", html=False)

    def test_cadastro_shows_selected_book_report_when_book_is_selected(self):
        work = Work.objects.create(
            code="book_0005",
            title="Filtered Book",
            original_language=self.language,
            author=self.author,
        )
        edition = Edition.objects.create(
            work=work,
            language=self.language,
            seal=self.seal,
            title="Filtered Book",
        )
        BookEditionTemplate.objects.create(
            book_code="book_0005",
            language="en",
            title="Filtered Book",
            author_name="Author Test",
            publication_year=2026,
            text_source_mode="txt",
            registration_status=BookEditionTemplate.STATUS_REGISTERED,
        )

        response = self.client.get(
            self.cadastro_url,
            {"book": "book_0005"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<th>Book code</th>", html=False)
        self.assertContains(response, "book_0005")
        self.assertContains(response, "Upload (sobrescrever com cuidado)")
        self.assertContains(response, reverse("edition_steps", kwargs={"edition_id": edition.id}))
        self.assertNotContains(response, "Selecione um livro e clique em <strong>Estado do livro</strong> para exibir o relatorio.", html=True)

    def test_cadastro_report_exposes_reedit_html_action(self):
        work = Work.objects.create(
            code="book_0014",
            title="HTML Reedit Book",
            original_language=self.language,
            author=self.author,
        )
        Edition.objects.create(
            work=work,
            language=self.language,
            seal=self.seal,
            title="HTML Reedit Book",
        )
        BookEditionTemplate.objects.create(
            book_code="book_0014",
            language="en",
            title="HTML Reedit Book",
            author_name="Author Test",
            publication_year=2026,
            text_source_mode="txt",
            registration_status=BookEditionTemplate.STATUS_REGISTERED,
        )

        response = self.client.get(self.cadastro_url, {"book": "book_0014"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reeditar (HTML)")
        self.assertContains(
            response,
            reverse("book_edition_upload", kwargs={"book_code": "book_0014", "language": "en"}) + "?force_source_format=html",
        )

    def test_cadastro_shows_book_012_in_single_selector(self):
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
        self.assertNotContains(response, reverse("pipeline_html_dashboard", kwargs={"edition_id": edition.id}))

    def test_cadastro_report_edit_action_always_uses_common_steps(self):
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

        response = self.client.get(self.cadastro_url, {"book": "book_013"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Continue Common Steps")
        self.assertContains(
            response,
            reverse("edition_steps", kwargs={"edition_id": edition.id}),
        )
        self.assertNotContains(
            response,
            reverse("pipeline_html_dashboard", kwargs={"edition_id": edition.id}),
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
        self.assertContains(response, "Bloco 01 · Entrada")
        self.assertContains(response, "Bloco 02 · Core")
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

    def test_saving_editorial_block_marks_pipeline_as_outdated(self):
        BookEditionTemplate.objects.update_or_create(
            book_code=self.work.code,
            language="en",
            defaults={
                "title": self.work.title,
                "author_name": self.author.name,
                "publication_year": 2026,
                "frontispiece_text": "Front",
                "copyright_text": "Rights",
                "about_edition_text": "About",
            },
        )

        response = self.client.post(
            reverse("frontmatter_template_edit", kwargs={"book_code": self.work.code, "language": "en"}),
            data={
                "book_code": self.work.code,
                "language": "en",
                "seal_name": self.seal.name,
                "title": self.work.title,
                "subtitle": "",
                "author_name": self.author.name,
                "publication_year": 2026,
                "imprint_name": self.seal.name,
                "city_name": "Rio de Janeiro",
                "country_name": "Brazil",
                "editor_name": "",
                "translator_name": "",
                "adapter_name": "",
                "frontispiece_text": "Front",
                "copyright_text": "Rights",
                "about_edition_text": "About",
                "has_preface": "on",
                "preface_text": "Updated preface",
                "has_introduction": "",
                "introduction_text": "",
                "has_epilogue": "",
                "epilogue_text": "",
                "about_contributor_text": "",
                "save_block": "preface",
                "confirm_overwrite": "1",
            },
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        template = BookEditionTemplate.objects.get(book_code=self.work.code, language="en")
        pipeline_state = EditionPipeline.objects.get(edition=self.edition)
        self.assertEqual(template.preface_text, "Updated preface")
        self.assertTrue(pipeline_state.editorial_changed)
        self.assertTrue(pipeline_state.build_outdated)
        self.assertIsNotNone(pipeline_state.last_editorial_update_at)

    @patch("pipeline.views.kdp_mode.build_frontmatter_files")
    @patch("pipeline.views.kdp_mode.build_merged_kdp_source")
    def test_build_creates_history_and_preserves_previous_versions(self, mock_build_merged, mock_frontmatter):
        build_dir = Path("data") / "builds" / self.work.code / "en"
        build_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / "BOOK.MD_FINAL").write_text("final md", encoding="utf-8")
        build_output = build_dir / "BOOK.BUILD.MD"

        def _fake_build(_edition):
            build_output.write_text("build md", encoding="utf-8")
            return build_output

        mock_build_merged.side_effect = _fake_build

        BookEditionTemplate.objects.update_or_create(
            book_code=self.work.code,
            language="en",
            defaults={
                "title": self.work.title,
                "author_name": self.author.name,
                "publication_year": 2026,
                "frontispiece_text": "Front",
                "copyright_text": "Rights",
                "about_edition_text": "About",
            },
        )
        pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=self.edition)
        pipeline_state.refined_at = timezone.now()
        pipeline_state.final_md_at = timezone.now()
        pipeline_state.save(update_fields=["refined_at", "final_md_at"])

        first = self.client.post(
            reverse("pipeline_run_edition_step", kwargs={"edition_id": self.edition.id, "step": "build"}),
            follow=False,
        )
        second = self.client.post(
            reverse("pipeline_run_edition_step", kwargs={"edition_id": self.edition.id, "step": "build"}),
            follow=False,
        )

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        history = list(EditionBuild.objects.filter(edition=self.edition, language_code="en").order_by("build_version"))
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].build_version, 1)
        self.assertEqual(history[1].build_version, 2)
        self.assertTrue(history[0].build_path)
        self.assertTrue(history[1].build_path)

    def test_edition_steps_exposes_four_blocks_and_editorial_languages(self):
        BookEditionTemplate.objects.update_or_create(
            book_code=self.work.code,
            language="en",
            defaults={
                "title": self.work.title,
                "author_name": self.author.name,
                "publication_year": 2026,
                "text_source_mode": "txt",
            },
        )
        response = self.client.get(
            f"{reverse('edition_steps', kwargs={'edition_id': self.edition.id})}?allow_html_to_common=1",
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bloco 01 - Entrada")
        self.assertContains(response, "Bloco 02 - Core do Sistema")
        self.assertContains(response, "Bloco 03 - Editorial e Assets")
        self.assertContains(response, "Bloco 04 - Finalizacao")
        self.assertContains(response, "Core oficial do sistema.")
        self.assertContains(response, "PT-BR")
        self.assertContains(response, "Français")
        self.assertContains(response, "Italiano")

    @patch("pipeline.views.kdp_mode.build_frontmatter_files")
    @patch("pipeline.views.kdp_mode.build_merged_kdp_source")
    def test_build_redirect_preserves_locked_language_after_step(self, mock_build_merged, mock_frontmatter):
        german = Language.objects.create(code="de", name="German", native_name="Deutsch", is_active=True)
        de_edition = Edition.objects.create(work=self.work, language=german, seal=self.seal)
        build_dir = Path("data") / "builds" / self.work.code / "de"
        build_dir.mkdir(parents=True, exist_ok=True)
        (build_dir / "BOOK.MD_FINAL").write_text("final md de", encoding="utf-8")
        (build_dir / "BOOK.BUILD.MD").write_text("build md de", encoding="utf-8")
        BookEditionTemplate.objects.update_or_create(
            book_code=self.work.code,
            language="de",
            defaults={
                "title": self.work.title,
                "author_name": self.author.name,
                "publication_year": 2026,
                "frontispiece_text": "Front DE",
                "copyright_text": "Rights DE",
                "about_edition_text": "About DE",
            },
        )
        pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=self.edition)
        pipeline_state.frontmatter_language = "de"
        pipeline_state.frontmatter_locked = True
        pipeline_state.md_language = "de"
        pipeline_state.save(update_fields=["frontmatter_language", "frontmatter_locked", "md_language"])

        mock_build_merged.return_value = build_dir / "BOOK.BUILD.MD"

        response = self.client.post(
            reverse("pipeline_run_edition_step", kwargs={"edition_id": self.edition.id, "step": "build"}),
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"{reverse('edition_steps', kwargs={'edition_id': de_edition.id})}?frontmatter_lang=de&frontmatter_lock=1",
        )

    def test_build_step_is_blocked_until_block_03_is_ready(self):
        response = self.client.post(
            reverse("pipeline_run_edition_step", kwargs={"edition_id": self.edition.id, "step": "build"}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bloco 04 bloqueado")


class PipelineBlockContractTests(CadastroSourceFormatRoutingTests):
    def test_block_02_is_declared_as_core_and_supports_six_isolated_languages(self):
        self.assertEqual(CORE_BLOCK_KEY, "bloco_02")
        self.assertEqual(len(CORE_ISOLATION_LANGUAGES), 6)
        self.assertEqual(set(CORE_ISOLATION_LANGUAGES), {"en", "ptbr", "es", "de", "it", "fr"})
        self.assertEqual(len(SYSTEM_BLOCKS), 4)

    @override_settings()
    def test_core_docker_isolation_defaults_to_all_block_02_languages(self):
        from pipeline.services import core_docker

        self.assertTrue(core_docker.should_run_in_docker("translate", "en"))
        self.assertTrue(core_docker.should_run_in_docker("translate", "en_philo"))
        self.assertTrue(core_docker.should_run_in_docker("translate", "it"))
        self.assertTrue(core_docker.should_run_in_docker("translate", "de"))
        self.assertTrue(core_docker.should_run_in_docker("refine", "de"))
        self.assertTrue(core_docker.should_run_in_docker("refine", "fr"))
        self.assertFalse(core_docker.should_run_in_docker("normalize", "de"))

    def test_core_docker_command_targets_language_specific_service(self):
        from pipeline.services import core_docker

        cmd = core_docker.build_docker_command(
            project_root=Path("/tmp/project"),
            edition_id=42,
            step="translate",
            language="de",
            target_language="de",
        )

        self.assertEqual(cmd[:7], ["docker", "compose", "-f", "/tmp/project/docker-compose.core.yml", "run", "--rm", "gaiden-core-de"])
        self.assertIn("--edition-id", cmd)
        self.assertIn("42", cmd)
        self.assertIn("--target-language", cmd)
        self.assertIn("de", cmd)

    def test_core_docker_maps_english_philosofer_to_english_service(self):
        from pipeline.services import core_docker

        cmd = core_docker.build_docker_command(
            project_root=Path("/tmp/project"),
            edition_id=42,
            step="translate",
            language="en_philo",
            target_language="en_philo",
        )

        self.assertEqual(cmd[:7], ["docker", "compose", "-f", "/tmp/project/docker-compose.core.yml", "run", "--rm", "gaiden-core-en"])
        self.assertIn("--target-language", cmd)
        self.assertIn("en_philo", cmd)

    def test_non_english_html_source_uses_own_language_as_processing_base(self):
        from pipeline.views import _processing_base_edition, build_pipeline01_steps

        german = Language.objects.create(
            code="de",
            name="Deutsch",
            native_name="Deutsch",
            is_active=True,
        )
        work = Work.objects.create(
            code="book_0200",
            title="German Source Book",
            original_language=german,
            author=self.author,
        )
        en_edition = Edition.objects.create(
            work=work,
            language=self.language,
            seal=self.seal,
            title="German Source Book EN",
        )
        de_edition = Edition.objects.create(
            work=work,
            language=german,
            seal=self.seal,
            title="German Source Book DE",
            raw_source_path=str(Path(settings.BASE_DIR).parent / "data" / "raw" / work.code / f"{work.code}_de_raw.html"),
        )
        BookEditionTemplate.objects.create(
            book_code=work.code,
            language="de",
            title="German Source Book DE",
            author_name="Author Test",
            publication_year=2026,
            text_source_mode="html",
            source_saved_path=de_edition.raw_source_path,
        )
        raw_dir = Path(settings.BASE_DIR).parent / "data" / "raw" / work.code
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{work.code}_de_raw.html").write_text("<html><body>DE</body></html>", encoding="utf-8")

        try:
            self.assertEqual(_processing_base_edition(de_edition).id, de_edition.id)
            steps = build_pipeline01_steps(de_edition)
            normalize_step = next(step for step in steps if step["key"] == "normalize")
            self.assertIn(f"data/normalized/{work.code}_de_v2.txt", normalize_step["outputs"][0])
        finally:
            shutil.rmtree(raw_dir, ignore_errors=True)
            shutil.rmtree(Path(settings.BASE_DIR).parent / "data" / "builds" / work.code, ignore_errors=True)
            shutil.rmtree(Path(settings.BASE_DIR).parent / "data" / "md" / work.code, ignore_errors=True)
            normalized = Path(settings.BASE_DIR).parent / "data" / "normalized" / f"{work.code}_de_v2.txt"
            if normalized.exists():
                normalized.unlink()
            en_edition.delete()
            de_edition.delete()
            work.delete()

    def test_translate_contracts_are_disabled(self):
        from pipeline.views import _select_contract_path

        with self.assertRaisesRegex(RuntimeError, "disabled"):
            _select_contract_path("de")

    def test_refine_contracts_are_disabled_but_profile_remains_configured(self):
        from pipeline.views import _default_refine_profile_for_language, _select_refine_contract

        self.assertEqual(_default_refine_profile_for_language("de"), "de_kaiser")
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            _select_refine_contract("de")

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
            reverse("book_edition_upload", kwargs={"book_code": self.work.code, "language": "en"}),
        )
        template = BookEditionTemplate.objects.get(book_code=self.work.code, language="en")
        self.assertEqual(template.registration_status, BookEditionTemplate.STATUS_REGISTERED)
        raw_path = self.raw_dir / f"{self.work.code}_en_raw.html"
        self.assertFalse(raw_path.exists())

        upload_response = self.client.post(
            reverse("book_edition_upload", kwargs={"book_code": self.work.code, "language": "en"}),
            data={
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
        template.refresh_from_db()
        self.assertEqual(template.registration_status, BookEditionTemplate.STATUS_READY_FOR_BLOCK_02)
        mock_frontmatter.assert_called_once()

    def test_upload_page_requires_saved_registration(self):
        response = self.client.get(
            reverse("book_edition_upload", kwargs={"book_code": "book_1234", "language": "en"}),
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("book_edition_new"))

    def test_upload_page_can_force_html_source_format_for_reedit(self):
        BookEditionTemplate.objects.create(
            book_code=self.work.code,
            language="en",
            title="Book Test",
            author_name="Author Test",
            publication_year=2026,
            text_source_mode="txt",
            registration_status=BookEditionTemplate.STATUS_REGISTERED,
        )

        response = self.client.get(
            reverse("book_edition_upload", kwargs={"book_code": self.work.code, "language": "en"})
            + "?force_source_format=html"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["upload_form"].initial["source_format"], "html")
        self.assertContains(response, "HTML aceita .html e .htm.")

    def test_html_edition_steps_stays_in_common_flow_after_source_md_exists(self):
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
        EditionPipeline.objects.update_or_create(
            edition=self.edition,
            defaults={"current_stage": "MD_SOURCE_READY"},
        )
        md_dir = self.root / "data" / "md" / self.work.code
        md_dir.mkdir(parents=True, exist_ok=True)
        (md_dir / f"{self.work.code}_en_source.md").write_text("# source", encoding="utf-8")
        self.addCleanup(lambda: shutil.rmtree(md_dir, ignore_errors=True))

        response = self.client.get(reverse("edition_steps", kwargs={"edition_id": self.edition.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bloco 02 · Core do Sistema")

    def test_public_domain_requires_original_dates(self):
        response = self.client.post(
            self.cadastro_url,
            data={
                "action": "save_metadata",
                "book_code": self.work.code,
                "language": "en",
                "title": "Book Test",
                "author_name": "Author Test",
                "publication_year": 2026,
                "work_kind": "PUBLIC_DOMAIN",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Data da publicacao original e obrigatoria")
        self.assertContains(response, "Data de falecimento do autor original e obrigatoria")

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
        self.repo_root = Path(__file__).resolve().parents[2]
        self.previous_storage_root = os.environ.get("GAIDEN_STORAGE_ROOT")
        os.environ["GAIDEN_STORAGE_ROOT"] = str(self.temp_root / "data")
        self.temp_web.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            self.repo_root / "gaiden" / "contracts",
            self.temp_root / "gaiden" / "contracts",
        )
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
        if self.previous_storage_root is None:
            os.environ.pop("GAIDEN_STORAGE_ROOT", None)
        else:
            os.environ["GAIDEN_STORAGE_ROOT"] = self.previous_storage_root
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
        self.assertContains(response, "Pagina 01", status_code=400)
        self.assertContains(response, "Corrija os erros do cadastro e tente novamente.", status_code=400)
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
        self.assertEqual(pipeline_state.current_stage, "SOURCE_EXTRACTED")
        raw_path = self.temp_root / "data" / "raw" / self.work.code / "en" / "source.html"
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
        self.assertEqual(pipeline_state.current_stage, "SOURCE_EXTRACTED")
        raw_path = self.temp_root / "data" / "raw" / self.work.code / "en" / "source.txt"
        self.assertTrue(raw_path.exists())


class MergeTranslatePreviewTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.temp_web = self.temp_root / "web"
        self.temp_web.mkdir(parents=True, exist_ok=True)
        self.settings_override = override_settings(BASE_DIR=self.temp_web)
        self.settings_override.enable()
        self.old_storage_root = os.environ.get("GAIDEN_STORAGE_ROOT")
        os.environ["GAIDEN_STORAGE_ROOT"] = str(self.temp_root / "data")

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
            text_source_mode="txt",
        )
        contract_dir = self.temp_root / "gaiden" / "contracts"
        contract_dir.mkdir(parents=True, exist_ok=True)
        (contract_dir / "en_modern_2025.json").write_text(
            json.dumps({"out_dir": "data/translated/book_0001/en_modern_2026"}),
            encoding="utf-8",
        )
        self.runtime_dir = self.temp_root / "data" / "translated" / "book_0103" / "en_modern_2026"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.merged_runtime_path = self.runtime_dir / "merged_en_modern_2026.txt"
        self.merged_runtime_path.write_text("Merged preview content.\n", encoding="utf-8")
        self.preview_url = reverse("preview_merge_translate", kwargs={"edition_id": self.edition.id})
        self.save_url = reverse("save_merge_translate_preview", kwargs={"edition_id": self.edition.id})

    def tearDown(self):
        if self.old_storage_root is None:
            os.environ.pop("GAIDEN_STORAGE_ROOT", None)
        else:
            os.environ["GAIDEN_STORAGE_ROOT"] = self.old_storage_root
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

    def test_preview_merge_translate_prefers_fixed_build_merge_when_runtime_is_missing(self):
        self.merged_runtime_path.unlink()
        build_dir = self.temp_root / "data" / "builds" / self.work.code / "en"
        build_dir.mkdir(parents=True, exist_ok=True)
        fixed_merge = build_dir / "merge_translate_en_fixed.txt"
        canonical_merge = build_dir / "merge_translate.txt"
        fixed_merge.write_text("Fixed build merge preview content.\n", encoding="utf-8")
        canonical_merge.write_text("Canonical build merge preview content.\n", encoding="utf-8")

        response = self.client.get(self.preview_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fixed build merge preview content.")
        self.assertEqual(response.context["md_path"], str(fixed_merge))


class MergePolidorPreviewTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.temp_web = self.temp_root / "web"
        self.temp_web.mkdir(parents=True, exist_ok=True)
        self.settings_override = override_settings(BASE_DIR=self.temp_web)
        self.settings_override.enable()
        self.old_storage_root = os.environ.get("GAIDEN_STORAGE_ROOT")
        os.environ["GAIDEN_STORAGE_ROOT"] = str(self.temp_root / "data")

        self.language = Language.objects.create(
            code="en",
            name="English",
            native_name="English",
            is_active=True,
        )
        self.author = Contributor.objects.create(name="Arthur Conan Doyle")
        self.seal = Seal.objects.create(slug="mantaquest-polidor-preview", name="MantaQuest")
        self.work = Work.objects.create(
            code="book_018",
            title="Sherlock Holmes - The Valley of Fear",
            original_language=self.language,
            author=self.author,
        )
        self.edition = Edition.objects.create(
            work=self.work,
            language=self.language,
            seal=self.seal,
            title="Sherlock Holmes - The Valley of Fear",
        )
        EditionPipeline.objects.create(edition=self.edition, translation_language="en")
        self.build_dir = self.temp_root / "data" / "builds" / self.work.code / "en"
        self.build_dir.mkdir(parents=True, exist_ok=True)
        self.preview_url = reverse("preview_merge_polidor", kwargs={"edition_id": self.edition.id})
        self.save_url = reverse("save_merge_polidor_preview", kwargs={"edition_id": self.edition.id})
        self.generic_preview_url = reverse("preview_merge_selected", kwargs={"edition_id": self.edition.id})

    def tearDown(self):
        if self.old_storage_root is None:
            os.environ.pop("GAIDEN_STORAGE_ROOT", None)
        else:
            os.environ["GAIDEN_STORAGE_ROOT"] = self.old_storage_root
        self.settings_override.disable()
        self.temp_dir.cleanup()

    def test_preview_merge_polidor_falls_back_to_latest_final_text(self):
        older = self.build_dir / "BOOK.MD_FINAL_v2.md"
        latest = self.build_dir / "BOOK.MD_FINAL_v3.md"
        older.write_text("Older final text.\n", encoding="utf-8")
        latest.write_text("Latest final text.\n", encoding="utf-8")
        os.utime(older, (100, 100))
        os.utime(latest, (200, 200))

        response = self.client.get(self.preview_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Latest final text.")
        self.assertEqual(response.context["md_path"], str(latest))

    def test_preview_merge_polidor_uses_newer_final_text_over_stale_formal_merge(self):
        stale_merge = self.build_dir / "merge_polidor.txt"
        latest = self.build_dir / "BOOK.MD_FINAL_v3.md"
        stale_merge.write_text("Stale polidor text.\n", encoding="utf-8")
        latest.write_text("Newer final text.\n", encoding="utf-8")
        os.utime(stale_merge, (100, 100))
        os.utime(latest, (200, 200))

        response = self.client.get(self.preview_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Newer final text.")
        self.assertEqual(response.context["md_path"], str(latest))

    def test_preview_merge_polidor_prefers_fixed_variant_when_present(self):
        canonical = self.build_dir / "merge_polidor.txt"
        fixed = self.build_dir / "merge_polidor_en_fixed.txt"
        canonical.write_text("Canonical polidor text.\n", encoding="utf-8")
        fixed.write_text("Fixed polidor text.\n", encoding="utf-8")

        response = self.client.get(self.preview_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fixed polidor text.")
        self.assertEqual(response.context["md_path"], str(fixed))

    def test_save_merge_polidor_copies_latest_final_text_when_formal_merge_is_missing(self):
        latest = self.build_dir / "kdp_merged_v3.md"
        latest.write_text("Final text to adjust.\n", encoding="utf-8")

        response = self.client.post(self.save_url)

        self.assertEqual(response.status_code, 302)
        saved_path = self.build_dir / "merge_polidor.txt"
        self.assertTrue(saved_path.exists())
        self.assertEqual(saved_path.read_text(encoding="utf-8"), "Final text to adjust.\n")

    def test_preview_merge_selected_prefers_fixed_translate_and_refine_variants(self):
        fixed_translate = self.build_dir / "merge_translate_en_fixed.txt"
        canonical_translate = self.build_dir / "merge_translate.txt"
        fixed_refine = self.build_dir / "merge_refine_en_fixed.txt"
        canonical_refine = self.build_dir / "merge_refine.txt"
        fixed_translate.write_text("Fixed translate text.\n", encoding="utf-8")
        canonical_translate.write_text("Canonical translate text.\n", encoding="utf-8")
        fixed_refine.write_text("Fixed refine text.\n", encoding="utf-8")
        canonical_refine.write_text("Canonical refine text.\n", encoding="utf-8")

        translate_response = self.client.get(self.generic_preview_url, {"merge_kind": "merge_translate"})
        refine_response = self.client.get(self.generic_preview_url, {"merge_kind": "merge_refine"})

        self.assertEqual(translate_response.status_code, 200)
        self.assertContains(translate_response, "Fixed translate text.")
        self.assertEqual(translate_response.context["md_path"], str(fixed_translate))

        self.assertEqual(refine_response.status_code, 200)
        self.assertContains(refine_response, "Fixed refine text.")
        self.assertEqual(refine_response.context["md_path"], str(fixed_refine))


class EnglishPhilosoferTranslateTests(TestCase):
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
        self.author = Contributor.objects.create(name="Marcus Aurelius")
        self.seal = Seal.objects.create(slug="mantaquest-philo", name="MantaQuest")
        self.work = Work.objects.create(
            code="book_0201",
            title="Meditations",
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
            text_source_mode="txt",
        )

        contract_dir = self.temp_root / "gaiden" / "contracts"
        contract_dir.mkdir(parents=True, exist_ok=True)
        (contract_dir / "en_modern_2025.json").write_text(
            json.dumps({"out_dir": "data/translated/book_0001/en_modern_2026"}),
            encoding="utf-8",
        )
        (contract_dir / "en_philosofer_2026.json").write_text(
            json.dumps(
                {
                    "out_dir": "data/translated/book_0001/en_philosofer_2026",
                    "io": {"lang_variant": "en_philosofer_2026"},
                    "system_prompt": "Translate Greek and Latin into English.",
                    "user_prompt": "Translate philosophical prose and translate Greek and Latin.\n\n{text}",
                }
            ),
            encoding="utf-8",
        )
        self.split_dir = self.temp_root / "data" / "chunks" / self.work.code / "split_01"
        self.split_dir.mkdir(parents=True, exist_ok=True)
        (self.split_dir / "0001.txt").write_text("logos\n", encoding="utf-8")
        heading_dir = self.temp_root / "data" / "chunks" / self.work.code / "heading_cleaner"
        heading_dir.mkdir(parents=True, exist_ok=True)
        (heading_dir / "clean.txt").write_text("clean", encoding="utf-8")

    def tearDown(self):
        self.settings_override.disable()
        self.temp_dir.cleanup()

    def test_select_contract_path_is_disabled_for_english_philosofer(self):
        from pipeline.views import _select_contract_path

        with self.assertRaisesRegex(RuntimeError, "disabled"):
            _select_contract_path("en_philo")

    def test_runtime_translate_contract_is_disabled_for_philosophy_variant(self):
        from pipeline.views import _build_runtime_translate_contract

        with self.assertRaisesRegex(RuntimeError, "disabled"):
            _build_runtime_translate_contract(self.edition, "en_philo")

    def test_edition_steps_hides_english_philosofer_option(self):
        response = self.client.get(reverse("edition_steps", kwargs={"edition_id": self.edition.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EN (modern)")
        self.assertContains(response, "PT-BR (portugues)")
        self.assertContains(response, "translate_pt_br_2026")
        self.assertNotContains(response, "English-Philosofer")
        self.assertNotContains(response, "English-Devotional")

    def test_preview_merge_translate_maps_legacy_philosophy_state_to_modern_en(self):
        EditionPipeline.objects.create(
            edition=self.edition,
            translation_language="en_philo",
        )
        from pipeline.views import _runtime_translate_dir_for_edition

        runtime_dir = _runtime_translate_dir_for_edition(self.edition, "en_philo")
        runtime_dir.mkdir(parents=True, exist_ok=True)
        merged_runtime_path = runtime_dir / "merged_en_modern_2026.txt"
        merged_runtime_path.write_text("Marcus preview content.\n", encoding="utf-8")

        response = self.client.get(reverse("preview_merge_translate", kwargs={"edition_id": self.edition.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Marcus preview content.")
        self.assertEqual(response.context["md_path"], str(merged_runtime_path))


class LegacyGermanBackfillTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.temp_web = self.temp_root / "web"
        self.temp_web.mkdir(parents=True, exist_ok=True)
        self.settings_override = override_settings(BASE_DIR=self.temp_web)
        self.settings_override.enable()

        self.language = Language.objects.create(
            code="de",
            name="Deutsch",
            native_name="Deutsch",
            is_active=True,
        )
        self.author = Contributor.objects.create(name="Arthur Conan Doyle")
        self.seal = Seal.objects.create(slug="mantaquest-legacy", name="MantaQuest Legacy")
        self.work = Work.objects.create(
            code="book_0001",
            title="The Adventures of Sherlock Holmes",
            original_language=self.language,
            author=self.author,
        )
        self.edition = Edition.objects.create(
            work=self.work,
            language=self.language,
            seal=self.seal,
            title="Die Abenteuer des Sherlock Holmes",
        )

        self.translated_dir = self.temp_root / "data" / "translated" / "book_0001" / "de_krimi_2025"
        self.translated_dir.mkdir(parents=True, exist_ok=True)
        (self.translated_dir / "0001.txt").write_text("Kapitel eins.\n", encoding="utf-8")
        (self.translated_dir / "merged_de_krimi_2025.txt").write_text(
            "Zusammengefuehrter Legacy-Text.\n",
            encoding="utf-8",
        )

        contract_dir = self.temp_root / "gaiden" / "contracts"
        contract_dir.mkdir(parents=True, exist_ok=True)
        (contract_dir / "en_de_2026.json").write_text(
            json.dumps({"out_dir": "data/translated/book_0001/de_modern_2026", "output": {"language": "de"}}),
            encoding="utf-8",
        )

    def tearDown(self):
        self.settings_override.disable()
        self.temp_dir.cleanup()

    def test_runtime_translate_dir_falls_back_to_legacy_variant(self):
        from pipeline.views import _runtime_translate_dir_for_edition

        resolved = _runtime_translate_dir_for_edition(self.edition, "de")

        self.assertEqual(resolved, self.translated_dir)

    def test_sync_legacy_merges_backfills_translate_from_legacy_variant(self):
        from pipeline.services.legacy_merges import sync_legacy_merges_from_translated

        sync_legacy_merges_from_translated(self.edition)

        build_merge = self.temp_root / "data" / "builds" / "book_0001" / "de" / "merge_translate.txt"
        self.assertTrue(build_merge.exists())
        self.assertEqual(build_merge.read_text(encoding="utf-8"), "Zusammengefuehrter Legacy-Text.\n")


class ChapterAgentSplitTests(TestCase):
    def test_split_merged_text_into_chapters_accepts_numbered_markdown_headings(self):
        from gaiden.chapter_agent_split import split_merged_text_into_chapters

        text = (
            "## 1. EIN SKANDAL IN BÖHMEN\n\n"
            + ("A" * 900)
            + "\n\n## 2. DER ROTHARIGEN-BUND\n\n"
            + ("B" * 900)
            + "\n"
        )

        chapters = split_merged_text_into_chapters(text)

        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0]["heading"], "## 1. EIN SKANDAL IN BÖHMEN")
        self.assertEqual(chapters[1]["heading"], "## 2. DER ROTHARIGEN-BUND")

    def test_split_merged_text_into_chapters_accepts_ordinal_book_headings(self):
        from gaiden.chapter_agent_split import split_merged_text_into_chapters

        text = (
            "## INTRODUCTION\n\n"
            + ("I" * 300)
            + "\n\n## THE FIRST BOOK\n\n"
            + ("A" * 900)
            + "\n\n## THE SECOND BOOK\n\n"
            + ("B" * 900)
            + "\n"
        )

        chapters = split_merged_text_into_chapters(text)

        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0]["heading"], "## THE FIRST BOOK")
        self.assertEqual(chapters[1]["heading"], "## THE SECOND BOOK")

    def test_split_merged_text_into_chapters_ignores_trailing_notes_sections(self):
        from gaiden.chapter_agent_split import split_merged_text_into_chapters

        text = (
            "## THE FIRST BOOK\n\n"
            + ("A" * 900)
            + "\n\n## THE SECOND BOOK\n\n"
            + ("B" * 900)
            + "\n\n## NOTES\n\nBOOK II \"Both to frequent\".\n\n## GLOSSARY\n\nGlossary entry.\n"
        )

        chapters = split_merged_text_into_chapters(text)

        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0]["heading"], "## THE FIRST BOOK")
        self.assertEqual(chapters[1]["heading"], "## THE SECOND BOOK")
        self.assertNotIn("## NOTES", chapters[1]["text"])
        self.assertNotIn("## GLOSSARY", chapters[1]["text"])


class BookCodeNormalizationTests(TestCase):
    def test_normalize_book_code_input_pads_short_numeric_codes(self):
        self.assertEqual(normalize_book_code_input("book_13"), "book_013")
        self.assertEqual(normalize_book_code_input("13"), "book_013")

    def test_normalize_book_code_input_preserves_existing_width_for_longer_codes(self):
        self.assertEqual(normalize_book_code_input("book_013"), "book_013")
        self.assertEqual(normalize_book_code_input("book_0006"), "book_0006")
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
        self.assertContains(response, "Bloco 01 · Entrada")
        self.assertContains(response, "Bloco 02 · Core")
        self.assertContains(response, "Bloco 03 · Editorial")
        self.assertContains(response, "Bloco 04 · Finalizacao")
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

    def test_preprod_promotes_roman_paragraph_chapters_even_with_title_headings(self):
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.raw_path.write_text(
            (
                "<html><body>"
                "<p>*** START OF THIS PROJECT GUTENBERG EBOOK ***</p>"
                "<h1>AT THE MOUNTAINS OF MADNESS</h1>"
                "<h2>By H. P. Lovecraft</h2>"
                "<p>Opening section.</p>"
                "<p class=\"ph1\">II.</p><p>Second section.</p>"
                "<p class=\"ph1\">III.</p><p>Third section.</p>"
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
        self.assertIn("<h2>CHAPTER 2</h2>", clean_html)
        self.assertIn("<h2>CHAPTER 3</h2>", clean_html)

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
        self.assertIn("CHAPTER 4", normalized_text)
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

    def test_normalize_promotes_standalone_roman_chapters_from_source_md(self):
        self.md_dir.mkdir(parents=True, exist_ok=True)
        self.source_md_path.write_text(
            (
                "# At the MOUNTAINS of MADNESS\n\n"
                "## By H. P. LOVECRAFT\n\n"
                "I am forced into speech because men of science have refused to follow my advice.\n\n"
                "------------------------------------------------------------------------\n\n"
                "II.\n\n"
                "The public knows of the Miskatonic Expedition through our frequent reports.\n\n"
                "III.\n\n"
                "It was natural that we should make our camp near the edge of the barrier.\n"
            ),
            encoding="utf-8",
        )

        response = self.client.post(self.normalize_url)

        self.assertEqual(response.status_code, 302)
        normalized_text = self.normalized_v2_path.read_text(encoding="utf-8")
        self.assertIn("CHAPTER 1", normalized_text)
        self.assertIn("CHAPTER 2", normalized_text)
        self.assertIn("CHAPTER 3", normalized_text)
        self.assertNotIn("\nII.\n", normalized_text)
        self.assertNotIn("\nIII.\n", normalized_text)
        texts = EditionText.objects.get(edition=self.edition)
        self.assertEqual(texts.normalized_text, normalized_text)

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
        self.translated_dir = self.root / "data" / "translated" / "book_9001" / "en_us"
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
        shutil.rmtree(self.root / "data" / "tmp_agent_chunks", ignore_errors=True)
        shutil.rmtree(self.root / "data" / "tmp_agent_return", ignore_errors=True)

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
        (self.translated_dir / "merged_en_modern_2026.txt").write_text("stale merged", encoding="utf-8")
        (self.translated_dir / "return_refine_en_us_2026").mkdir(parents=True, exist_ok=True)
        ((self.translated_dir / "return_refine_en_us_2026") / "0001.txt").write_text(
            "stale refine", encoding="utf-8"
        )
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

    def test_refine_agent_handoff_uses_source_chunks_without_runtime_contract(self):
        from pipeline.views import _prepare_refine_agent_handoff

        self.client.post(self.heading_url)
        self.client.post(reverse("pipeline_chunk_run", kwargs={"edition_id": self.edition.id}))
        self.translated_dir.mkdir(parents=True, exist_ok=True)
        (self.translated_dir / "0001.txt").write_text(
            "Holmes spoke plainly.\n\nWatson listened carefully.",
            encoding="utf-8",
        )

        source_dir, source_label, out_dir_path, profile_cfg, profile = _prepare_refine_agent_handoff(
            self.edition,
            "en",
            refine_profile="ingles_flex",
        )

        self.assertEqual(source_dir, self.translated_dir)
        self.assertEqual(source_label, "translate_chunks")
        self.assertEqual(out_dir_path, self.translated_dir / "return_refine_en_us_2026")
        self.assertEqual(profile, "refine_en_us_2026")
        self.assertEqual(profile_cfg["agent_name"], "refine_en_us_2026")
        self.assertFalse((self.edition_core_dir / "contract_refine_en.json").exists())

    def test_runtime_refine_contract_is_disabled(self):
        from pipeline.views import _build_runtime_refine_contract

        with self.assertRaisesRegex(RuntimeError, "Legacy hosted Refine contracts are disabled"):
            _build_runtime_refine_contract(self.edition, "en")

    def test_prompt_echo_line_is_stripped_from_generated_chunk_output(self):
        from gaiden.translate import sanitize_generated_chunk_text

        cleaned = sanitize_generated_chunk_text(
            "Please provide the passage from *The People of the Black Circle* that you would like me to modernize.\n\nBody text."
        )

        self.assertEqual(cleaned, "Body text.")

    def test_runtime_translate_contract_is_disabled_for_large_chunks(self):
        from pipeline.views import _build_runtime_translate_contract

        self.client.post(self.heading_url)
        self.client.post(reverse("pipeline_chunk_run", kwargs={"edition_id": self.edition.id}))
        large_chunk = self.split_dir / "0003.txt"
        large_chunk.write_text(("A long translated paragraph. " * 320), encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "disabled"):
            _build_runtime_translate_contract(self.edition, "en")

    def test_refine_agent_handoff_keeps_large_chunks_on_direct_source(self):
        from pipeline.views import _prepare_refine_agent_handoff

        self.client.post(self.heading_url)
        self.client.post(reverse("pipeline_chunk_run", kwargs={"edition_id": self.edition.id}))
        self.translated_dir.mkdir(parents=True, exist_ok=True)
        (self.translated_dir / "0001.txt").write_text(
            ("A long refined paragraph. " * 320),
            encoding="utf-8",
        )

        source_dir, _source_label, out_dir_path, _profile_cfg, _profile = _prepare_refine_agent_handoff(
            self.edition, "en"
        )

        self.assertEqual(source_dir, self.translated_dir)
        self.assertEqual(out_dir_path, self.translated_dir / "return_refine_en_us_2026")
        self.assertFalse((self.edition_core_dir / "contract_refine_en.json").exists())

    def test_agent_refine_return_writes_report_and_rejects_incomplete_merge(self):
        from gaiden.tools.aldebaran_refine_return import run_aldebaran_refine_return

        chunk_dir = self.root / "data" / "tmp_agent_chunks"
        out_dir = self.root / "data" / "tmp_agent_return"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        (chunk_dir / "0001.txt").write_text(
            "The barbarian looked back toward the ruins.\n\nThe stars were already paling for dawn.",
            encoding="utf-8",
        )

        with patch("gaiden.tools.aldebaran_refine_return.openai_healthcheck", return_value=(True, "ok")):
            with patch("gaiden.tools.aldebaran_refine_return.call_agent_text", return_value="Refined text."):
                result = run_aldebaran_refine_return(
                    chunk_dir=chunk_dir,
                    out_dir=out_dir,
                    agent_name="Alamaguederaz",
                    merge_name="merge_refine_en.txt",
                )

        self.assertEqual((out_dir / "0001.txt").read_text(encoding="utf-8"), "Refined text.\n")
        self.assertTrue((out_dir / "agent_refine_return_report.json").exists())
        self.assertEqual(Path(result["merge_path"]), out_dir / "merge_refine_en.txt")

    def test_runtime_translate_contract_is_generic_and_not_sherlock_specific(self):
        from pipeline.views import _build_runtime_translate_contract

        self.client.post(self.heading_url)
        self.client.post(reverse("pipeline_chunk_run", kwargs={"edition_id": self.edition.id}))

        with self.assertRaisesRegex(RuntimeError, "disabled"):
            _build_runtime_translate_contract(self.edition, "en")

    def test_translate_falls_back_to_gpt52_when_gpt54_returns_no_response(self):
        from gaiden.translate import run_translate_with_contract

        temp_root = Path(tempfile.mkdtemp(prefix="translate_fallback_"))
        self.addCleanup(lambda: shutil.rmtree(temp_root, ignore_errors=True))

        chunk_dir = temp_root / "chunks"
        out_dir = temp_root / "translated" / "en_modern_2026"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        (chunk_dir / "0001.txt").write_text("Source chunk.", encoding="utf-8")

        contract_path = temp_root / "contract_translate_en.json"
        contract_path.write_text(
            json.dumps(
                {
                    "chunk_dir": str(chunk_dir),
                    "out_dir": str(out_dir),
                    "model": "gpt-5.4",
                    "fallback_model": "gpt-5.2",
                    "system_prompt": "Rewrite carefully.",
                    "user_prompt": "{text}",
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(RuntimeError, "disabled"):
            run_translate_with_contract(contract_path)

    def test_translate_retries_same_model_when_chunk_output_is_truncated(self):
        from gaiden.translate import run_translate_with_contract

        temp_root = Path(tempfile.mkdtemp(prefix="translate_retry_"))
        self.addCleanup(lambda: shutil.rmtree(temp_root, ignore_errors=True))

        chunk_dir = temp_root / "chunks"
        out_dir = temp_root / "translated" / "de_modern_2026"
        chunk_dir.mkdir(parents=True, exist_ok=True)
        source_text = (
            '"Oh, you value my assistance too highly," said Sherlock Holmes lightly. '
            '"You cannot expect me to believe that you have read all this from his old watch! '
            'It is unkind, and, to speak plainly, has a touch of charlatanism in it."'
        )
        (chunk_dir / "0001.txt").write_text(source_text, encoding="utf-8")

        contract_path = temp_root / "contract_translate_de.json"
        contract_path.write_text(
            json.dumps(
                {
                    "chunk_dir": str(chunk_dir),
                    "out_dir": str(out_dir),
                    "target_language": "de",
                    "model": "gpt-5.4",
                    "fallback_model": "gpt-5.2",
                    "max_output_tokens": 1800,
                    "system_prompt": "Translate carefully.",
                    "user_prompt": "{text}",
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(RuntimeError, "disabled"):
            run_translate_with_contract(contract_path)

    def test_german_closing_quote_counts_as_complete_chunk_boundary(self):
        from gaiden.translate import chunk_truncation_reason

        source_text = (
            '"You cannot expect me to believe that you have read all this from his old watch! '
            'It is unkind, and, to speak plainly, has a touch of charlatanism in it."'
        )
        candidate_text = (
            '„Sie können doch nicht erwarten, dass ich glaube, Sie hätten all das aus seiner alten Uhr herausgelesen! '
            'Das ist unfreundlich und, offen gesagt, hat einen Anflug von Scharlatanerie.“'
        )

        self.assertIsNone(chunk_truncation_reason(source_text, candidate_text))

    def test_german_single_closing_quote_counts_as_complete_chunk_boundary(self):
        from gaiden.translate import chunk_truncation_reason

        source_text = (
            "'Not so fast,' said I, growing colder as he got hot. "
            "'I must have the consent of my three comrades. I tell you that it is four or none with us.'"
        )
        candidate_text = (
            "‚Nicht so schnell‘, sagte ich und wurde kälter, je hitziger er wurde. "
            "‚Ich muss die Zustimmung meiner drei Kameraden haben. Ich sage Ihnen, bei uns heißt es vier oder keiner.‘"
        )

        self.assertIsNone(chunk_truncation_reason(source_text, candidate_text))

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

        refine_dir = self.translated_dir / "return_refine_en_us_2026"
        refine_dir.mkdir(parents=True, exist_ok=True)
        (refine_dir / "0001.txt").write_text("partial refine", encoding="utf-8")
        (refine_dir / "merged_refine_en_us_2026.txt").write_text("partial merged refine", encoding="utf-8")

        response = self.client.get(self.steps_url)
        html = response.content.decode("utf-8")

        self.assertContains(response, "chunks=1/2")
        self.assertRegex(html, r'<button[^>]*disabled[^>]*>\s*Rodar MergeRefine\s*</button>')
        self.assertIn("refine completo com merge correspondente", html)

    def test_steps_show_refine_profile_selector(self):
        response = self.client.get(self.steps_url)

        self.assertContains(response, 'name="refine_profile"')
        self.assertContains(response, "Refine_US_EN (2026) - refine_en_us_2026")
        self.assertNotContains(response, "Ingles neutro - Aldebaran")
        self.assertNotContains(response, "Ingles flex - Alamaguederaz")
        self.assertNotContains(response, "Inglês filosofia - HeadingCleaner")

    def test_steps_reflect_saved_refine_profile(self):
        EditionPipeline.objects.update_or_create(
            edition=self.edition,
            defaults={"refine_profile": "ingles_flex"},
        )

        response = self.client.get(self.steps_url)

        self.assertContains(response, "6) Refine (Refine_US_EN (2026))")
        self.assertContains(response, '<option value="refine_en_us_2026" selected>', html=False)

    def test_pipeline01_step_order_is_fixed(self):
        response = self.client.get(self.steps_url)
        html = response.content.decode("utf-8")

        expected = [
            "1) Normalize",
            "2) HeadingCleaner (Mechanical)",
            "3) Split/Chunk",
            "4) Translate (Agent)",
            "5) Split by Chapter (merge_translate)",
            "6) Refine (Refine_US_EN (2026))",
            "7) Merge/Finalize",
            "8) Etapa 3 · Split by Chapter (merge_refine)",
            "9) Polidor Agent",
        ]
        positions = [html.find(item) for item in expected]
        self.assertTrue(all(pos >= 0 for pos in positions))
        self.assertEqual(positions, sorted(positions))
        self.assertContains(response, "Bloco 03 · Editorial e Assets")
        self.assertContains(response, "Pre-flight")
        self.assertNotContains(response, "Lock translate")
        self.assertNotContains(response, "Lock refine")
        self.assertNotContains(response, "Lock polish")
        self.assertContains(response, 'name="polish_agent_name"')
        self.assertContains(response, "Preview Merge Polidor")
        self.assertContains(response, "Salvar Merge Polidor")
        self.assertNotContains(response, "Preview Merge Translate")
        self.assertNotContains(response, "Salvar Merge Translate")

    def test_translate_disabled_without_heading_cleaner(self):
        response = self.client.get(self.steps_url)
        html = response.content.decode("utf-8")

        self.assertRegex(html, r'id="btn_translate"[^>]*disabled')

    def test_chunk_post_is_blocked_without_heading_cleaner(self):
        shutil.rmtree(self.split_dir, ignore_errors=True)
        response = self.client.post(reverse("pipeline_chunk_run", kwargs={"edition_id": self.edition.id}))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.steps_url)
        self.assertFalse(self.split_dir.exists())

    def test_translate_shows_agent_route(self):
        response = self.client.get(self.steps_url)

        self.assertContains(response, "Agent")
        self.assertContains(response, 'name="translate_agent_name"')
        self.assertContains(response, "HeadingCleaner")
        self.assertContains(response, "PT-BR (portugues)")
        self.assertContains(response, "translate_pt_br_2026")
        self.assertNotContains(response, "English-Philosofer")
        self.assertNotContains(response, "English-Devotional")

    def test_french_steps_show_coulhon_refine_profile(self):
        french = Language.objects.create(
            code="fr",
            name="French",
            native_name="Francais",
            is_active=True,
        )
        french_edition = Edition.objects.create(
            work=self.work,
            language=french,
            seal=self.seal,
        )
        BookEditionTemplate.objects.create(
            book_code=self.work.code,
            language="fr",
            title=self.work.title,
            author_name=self.author.name,
            publication_year=2026,
            text_source_mode="html",
        )

        response = self.client.get(
            f"{reverse('edition_steps', kwargs={'edition_id': french_edition.id})}?allow_html_to_common=1"
        )

        self.assertContains(response, 'name="refine_profile"')
        self.assertContains(response, "Francais Le Grand Coulhon - Le Grand Coulhon")
        self.assertContains(response, "Francais Le Gran Colhoun - Le_Gran_Colhoun")
        self.assertNotContains(response, "Ingles neutro - Aldebaran")
        self.assertNotContains(response, "Ingles flex - Alamaguederaz")

    def test_ptbr_steps_show_cacique_tibirica_refine_profile(self):
        ptbr = Language.objects.create(
            code="ptbr",
            name="Portuguese",
            native_name="Portugues",
            is_active=True,
        )
        ptbr_edition = Edition.objects.create(
            work=self.work,
            language=ptbr,
            seal=self.seal,
        )
        BookEditionTemplate.objects.create(
            book_code=self.work.code,
            language="ptbr",
            title=self.work.title,
            author_name=self.author.name,
            publication_year=2026,
            text_source_mode="html",
        )

        response = self.client.get(
            f"{reverse('edition_steps', kwargs={'edition_id': ptbr_edition.id})}?allow_html_to_common=1"
        )

        self.assertContains(response, 'name="refine_profile"')
        self.assertContains(response, "Portugues Cacique Tibiriça - Cacique Tibiriça")
        self.assertNotContains(response, "Ingles neutro - Aldebaran")
        self.assertNotContains(response, "Ingles flex - Alamaguederaz")

    def test_french_steps_show_frances_polidor_option(self):
        french = Language.objects.create(
            code="fr",
            name="French",
            native_name="Francais",
            is_active=True,
        )
        french_edition = Edition.objects.create(
            work=self.work,
            language=french,
            seal=self.seal,
        )
        BookEditionTemplate.objects.create(
            book_code=self.work.code,
            language="fr",
            title=self.work.title,
            author_name=self.author.name,
            publication_year=2026,
            text_source_mode="html",
        )

        response = self.client.get(
            f"{reverse('edition_steps', kwargs={'edition_id': french_edition.id})}?allow_html_to_common=1"
        )

        self.assertContains(response, 'name="polish_agent_name"')
        self.assertContains(response, "Francês_Polidor")
        self.assertNotContains(response, '<option value="English Polidor" selected>', html=False)


    def test_split_refine_by_chapter_unlocks_when_language_variant_has_canonical_merge_refine_clean(self):
        french = Language.objects.create(
            code="fr",
            name="French",
            native_name="Francais",
            is_active=True,
        )
        french_edition = Edition.objects.create(
            work=self.work,
            language=french,
            seal=self.seal,
        )
        EditionPipeline.objects.update_or_create(
            edition=french_edition,
            defaults={
                "current_stage": "REFINED",
                "translation_language": "fr",
                "md_language": "fr",
                "refine_profile": "fr_colhoun",
                "translated_at": timezone.now(),
                "refined_at": timezone.now(),
            },
        )
        BookEditionTemplate.objects.create(
            book_code=self.work.code,
            language="fr",
            title=self.work.title,
            author_name=self.author.name,
            publication_year=2026,
            text_source_mode="html",
        )
        french_build_dir = self.root / "data" / "builds" / self.work.code / "fr"
        french_build_dir.mkdir(parents=True, exist_ok=True)
        (french_build_dir / "merge_translate.txt").write_text("merged translate", encoding="utf-8")
        (french_build_dir / "merge_refine.txt").write_text("merged refine", encoding="utf-8")
        refine_parts = french_build_dir / "split_by_chapter" / "return_le_gran_colhoun"
        refine_parts.mkdir(parents=True, exist_ok=True)
        (refine_parts / "chapter_01_part_01.txt").write_text("refined chapter", encoding="utf-8")
        translated_clean = self.root / "data" / "translated" / self.work.code / "fr"
        translated_clean.mkdir(parents=True, exist_ok=True)
        (translated_clean / "merge_refine_clean.txt").write_text("canonical refine clean", encoding="utf-8")

        response = self.client.get(
            f"{reverse('edition_steps', kwargs={'edition_id': french_edition.id})}?allow_html_to_common=1"
        )

        self.assertNotContains(response, "8) Etapa 3 · Split by Chapter (merge_refine) bloqueado")

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
            "4) Translate (Agent)",
            "5) Split by Chapter (merge_translate)",
            "6) Refine (Refine_US_EN (2026))",
            "7) Merge/Finalize",
        ]
        positions = [html.find(item) for item in expected]
        self.assertTrue(all(pos >= 0 for pos in positions))
        self.assertEqual(positions, sorted(positions))
        self.assertContains(response, "Bloco 03 · Editorial e Assets")
        self.assertContains(response, "Pre-flight")

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
        refine_dir = self.translated_dir / "return_refine_en_us_2026"
        refine_dir.mkdir(parents=True, exist_ok=True)
        (refine_dir / "0001.txt").write_text("refined 1", encoding="utf-8")
        (refine_dir / "merged_refine_en_us_2026.txt").write_text("merged refine", encoding="utf-8")
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

    def test_preflight_source_prefers_merge_polidor_when_available(self):
        from pipeline.services import preflight

        self.build_dir.mkdir(parents=True, exist_ok=True)
        polidor = self.build_dir / "merge_polidor.txt"
        polidor.write_text("polidor text", encoding="utf-8")
        translated_clean = self.root / "data" / "translated" / self.book_code / "merge_refine_clean.txt"
        translated_clean.parent.mkdir(parents=True, exist_ok=True)
        translated_clean.write_text("older canonical refine text", encoding="utf-8")

        self.assertEqual(preflight._pick_source_text(self.edition), polidor)

    def test_preflight_heuristic_does_not_flag_pagebreak_markers_as_amputated(self):
        from pipeline.services import preflight

        report = preflight._heuristic_analysis(
            "# Title\n\n"
            "::: pagebreak\n"
            ":::\n\n"
            "## Chapter 01\n\n"
            "A complete paragraph follows the markdown marker."
        )

        self.assertEqual(report["critical"], [])

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
        refine_dir = self.translated_dir / "return_refine_en_us_2026"
        refine_dir.mkdir(parents=True, exist_ok=True)
        (refine_dir / "0001.txt").write_text("refined 1", encoding="utf-8")
        (refine_dir / "merged_refine_en_us_2026.txt").write_text("merged refine", encoding="utf-8")
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
        refine_dir = self.translated_dir / "return_refine_en_us_2026"
        refine_dir.mkdir(parents=True, exist_ok=True)
        (refine_dir / "0001.txt").write_text("refined 1", encoding="utf-8")
        (refine_dir / "merged_refine_en_us_2026.txt").write_text("merged refine", encoding="utf-8")
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
        self.assertContains(response, "Pre-producao (Pre-flight)")
        self.assertContains(response, "Rerodar Pre-flight")
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
        refine_dir = self.translated_dir / "return_refine_en_us_2026"
        refine_dir.mkdir(parents=True, exist_ok=True)
        (refine_dir / "0001.txt").write_text("source chunk closes with The", encoding="utf-8")
        (refine_dir / "merged_refine_en_us_2026.txt").write_text("bad merged refine", encoding="utf-8")

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

        refine_dir = self.translated_dir / "return_refine_en_us_2026"
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

    def test_insert_images_does_not_treat_author_heading_as_chapter(self):
        self.pre_edition_path.write_text(
            (
                "# Title\n\n"
                "## By Robert E. Howard\n\n"
                "# Chapter 01 - The Adventure of the Empty House\n\n"
                "Body of chapter one.\n"
            ),
            encoding="utf-8",
        )

        response = self.client.post(
            self.steps_url,
            data={"action": "insert_images"},
        )

        self.assertEqual(response.status_code, 302)
        updated_md = self.pre_edition_path.read_text(encoding="utf-8")
        self.assertNotIn("## By Robert E. Howard\n{{IMAGE:CH01:01}}", updated_md)
        self.assertIn("# Chapter 01 - The Adventure of the Empty House\n{{IMAGE:CH01:01}}", updated_md)

    def test_insert_images_keeps_sequential_placeholders_when_part_two_restarts_chapters(self):
        self.pre_edition_path.write_text(
            (
                "## Part I - The Tragedy of Birlstone\n\n"
                "# Chapter 01 - The Warning\n\n"
                "Body 1.\n\n"
                "# Chapter 02 - Sherlock Holmes Discourses\n\n"
                "Body 2.\n\n"
                "## Part II - The Scowrers\n\n"
                "# Chapter 01 - The Man\n\n"
                "Body 3.\n\n"
                "# Chapter 02 - The Bodymaster\n\n"
                "Body 4.\n"
            ),
            encoding="utf-8",
        )

        response = self.client.post(
            self.steps_url,
            data={"action": "insert_images"},
        )

        self.assertEqual(response.status_code, 302)
        updated_md = self.pre_edition_path.read_text(encoding="utf-8")
        self.assertIn("# Chapter 01 - The Warning\n{{IMAGE:CH01:01}}", updated_md)
        self.assertIn("# Chapter 02 - Sherlock Holmes Discourses\n{{IMAGE:CH02:01}}", updated_md)
        self.assertIn("# Chapter 01 - The Man\n{{IMAGE:CH03:01}}", updated_md)
        self.assertIn("# Chapter 02 - The Bodymaster\n{{IMAGE:CH04:01}}", updated_md)


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

        refined_dir = self.temp_root / "data" / "translated" / "book_0201" / "en_modern_2026" / "return_aldebaran"
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

    def test_txt_to_md_promotes_french_livre_markers_to_chapter_headings(self):
        from pipeline.services import md_transform

        txt_path = self.temp_root / "merge_polish.txt"
        txt_path.write_text(
            (
                "LIVRE 01\n\n"
                "01 — Premier aphorisme.\n\n"
                "02 — Deuxieme aphorisme.\n\n"
                "LIVRE 02\n\n"
                "01 — Troisieme aphorisme.\n"
            ),
            encoding="utf-8",
        )

        out_path = self.temp_root / "BOOK.PRE_EDITION.md"
        md_transform.pre_edition_txt_to_md(
            txt_path,
            out_path,
            md_transform.PreEditionConfig(
                title="Meditations",
                book_code="book_0024",
                language="fr",
            ),
        )

        output = out_path.read_text(encoding="utf-8")
        self.assertIn("# Livre 01", output)
        self.assertIn("# Livre 02", output)
        self.assertIn("01 — Premier aphorisme.", output)
        self.assertNotIn("# Chapter 01 - Livre 01", output)

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

        refined_dir = self.temp_root / "data" / "translated" / "book_0012" / "en_modern_2026" / "return_aldebaran"
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

    def test_chunk_boundary_contract_strips_raw_heading_artifacts_from_translated_chunks(self):
        from pipeline.services import md_transform

        split_dir = self.temp_root / "data" / "chunks" / "book_0015" / "split_01"
        split_dir.mkdir(parents=True, exist_ok=True)
        (split_dir / "0001.txt").write_text("# SHADOWS IN ZAMBOULA", encoding="utf-8")
        (split_dir / "0002.txt").write_text("## By Robert E. Howard", encoding="utf-8")
        (split_dir / "0003.txt").write_text("## 1 A Drum Begins", encoding="utf-8")
        (split_dir / "0004.txt").write_text("## 2 The Night Skulkers", encoding="utf-8")

        translated_dir = self.temp_root / "data" / "translated" / "book_0015" / "en_modern_2026"
        translated_dir.mkdir(parents=True, exist_ok=True)
        (translated_dir / "0001.txt").write_text("Front matter.", encoding="utf-8")
        (translated_dir / "0002.txt").write_text("By Robert E. Howard", encoding="utf-8")
        (translated_dir / "0003.txt").write_text(
            "## 1 A Drum Begins\n\n1 A Drum Begins\n\nOpening one.",
            encoding="utf-8",
        )
        (translated_dir / "0004.txt").write_text(
            "## 2 The Night Skulkers\n\nOpening two.",
            encoding="utf-8",
        )

        txt_path = self.temp_root / "merge_polish.txt"
        txt_path.write_text("placeholder", encoding="utf-8")

        output = md_transform._markdown_from_chunk_boundaries(
            txt_path,
            md_transform.PreEditionConfig(
                title="Conan - Shadows in Zambula",
                book_code="book_015",
                language="en",
            ),
        )

        self.assertIn("# Chapter 01 - A Drum Begins", output)
        self.assertIn("# Chapter 02 - The Night Skulkers", output)
        self.assertNotIn("## 1 A Drum Begins", output)
        self.assertNotIn("## 2 The Night Skulkers", output)
        self.assertNotIn("\n1 A Drum Begins\n", output)

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

    def test_book_016_expected_titles_resolve_from_numeric_chapters(self):
        from pipeline.services import md_transform

        cfg = md_transform.PreEditionConfig(
            title="At the Mountains of Madness",
            book_code="book_016",
            language="en",
        )

        self.assertEqual(
            md_transform._resolve_contract_title_from_heading("CHAPTER 1", cfg),
            "The Warning from Miskatonic",
        )
        self.assertEqual(
            md_transform._resolve_contract_title_from_heading("CHAPTER 8", cfg),
            "The Doom of the Elder City",
        )
        self.assertEqual(
            md_transform._resolve_contract_title_from_heading("CHAPTER XII", cfg),
            "The Last Glimpse",
        )


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
        self.assertIn("# Chapter 01 - First Case", merged_text)
        self.assertNotIn("MARKER CONTRACT BOOK", merged_text)
        self.assertNotIn("First published 1927", merged_text)
        self.assertNotIn("\nCONTENTS\n", merged_text)

    def test_build_kdp_does_not_label_empty_preface(self):
        from editorial import kdp_mode

        self.pre_edition_path.write_text(
            (
                "::: center\n"
                "# Marker Contract Book\n\n"
                "## Illustrated Edition\n"
                ":::\n\n"
                "# Chapter 01 - First Case\n\n"
                "Body text.\n"
            ),
            encoding="utf-8",
        )

        merged_path = kdp_mode.build_merged_kdp_source(self.edition)
        merged_text = merged_path.read_text(encoding="utf-8")

        self.assertNotIn("# Adapted Preface", merged_text)
        self.assertIn("# Chapter 01 - First Case", merged_text)

    def test_build_kdp_recognizes_french_livre_headings_as_miolo(self):
        from editorial import kdp_mode

        fr_language = Language.objects.create(
            code="fr",
            name="French",
            native_name="Français",
            is_active=True,
        )
        fr_edition = Edition.objects.create(
            work=self.work,
            language=fr_language,
            seal=self.seal,
        )
        fr_build_dir = Path("data") / "builds" / self.work.code / "fr"
        fr_build_dir.mkdir(parents=True, exist_ok=True)
        (fr_build_dir / "BOOK.PRE_EDITION.md").write_text(
            (
                "::: center\n"
                "# Les Meditations\n"
                ":::\n\n"
                "# Livre 01\n\n"
                "01 — Premier aphorisme.\n\n"
                "# Livre 02\n\n"
                "01 — Deuxieme aphorisme.\n"
            ),
            encoding="utf-8",
        )

        merged_text = kdp_mode.build_merged_kdp_source(fr_edition).read_text(encoding="utf-8")

        self.assertNotIn("# Adapted Preface", merged_text)
        self.assertIn("# Livre 01", merged_text)
        self.assertIn("# Livre 02", merged_text)
        self.assertIn("**01 —** Premier aphorisme.\n\n# Livre 02", merged_text)

    def test_build_kdp_preserves_french_numbered_aphorism_spacing_and_caps(self):
        from editorial import kdp_mode

        fr_language = Language.objects.create(
            code="fr",
            name="French",
            native_name="Français",
            is_active=True,
        )
        fr_edition = Edition.objects.create(
            work=self.work,
            language=fr_language,
            seal=self.seal,
        )
        fr_build_dir = Path("data") / "builds" / self.work.code / "fr"
        fr_build_dir.mkdir(parents=True, exist_ok=True)
        (fr_build_dir / "BOOK.PRE_EDITION.md").write_text(
            (
                "# Livre 01\n\n"
                "01 — premier aphorisme.\n\n"
                "02 — deuxieme aphorisme.\n\n"
                "03 — Troisieme aphorisme.\n"
            ),
            encoding="utf-8",
        )

        merged_text = kdp_mode.build_merged_kdp_source(fr_edition).read_text(encoding="utf-8")

        self.assertIn("**01 —** Premier aphorisme.\n\n**02 —** Deuxieme aphorisme.", merged_text)
        self.assertIn("\n\n**03 —** Troisieme aphorisme.", merged_text)

    def test_build_kdp_keeps_blank_lines_around_latex_pagebreaks_before_french_livre(self):
        from editorial import kdp_mode

        fr_language = Language.objects.create(
            code="fr",
            name="French",
            native_name="Français",
            is_active=True,
        )
        fr_edition = Edition.objects.create(
            work=self.work,
            language=fr_language,
            seal=self.seal,
        )
        fr_build_dir = Path("data") / "builds" / self.work.code / "fr"
        fr_build_dir.mkdir(parents=True, exist_ok=True)
        (fr_build_dir / "BOOK.PRE_EDITION.md").write_text(
            (
                "# Livre 01\n\n"
                "01 — Premier aphorisme.\n\n"
                "\\newpage\n"
                "# Livre 02\n\n"
                "01 — Deuxieme aphorisme.\n"
            ),
            encoding="utf-8",
        )

        merged_text = kdp_mode.build_merged_kdp_source(fr_edition).read_text(encoding="utf-8")

        self.assertIn("**01 —** Premier aphorisme.\n\n\\newpage\n\n# Livre 02", merged_text)

    def test_build_kdp_formats_french_glossary_identifier_entries_inline(self):
        from editorial import kdp_mode

        fr_language = Language.objects.create(
            code="fr",
            name="French",
            native_name="Français",
            is_active=True,
        )
        fr_edition = Edition.objects.create(
            work=self.work,
            language=fr_language,
            seal=self.seal,
        )
        fr_build_dir = Path("data") / "builds" / self.work.code / "fr"
        fr_build_dir.mkdir(parents=True, exist_ok=True)
        (fr_build_dir / "BOOK.PRE_EDITION.md").write_text(
            (
                "# Livre 01\n\n"
                "Harmodios est mentionné dans le texte.\n\n"
                "GLOSSAIRE\n\n"
                "1. Philosophes, auteurs, maîtres et personnages grecs\n\n"
                "G037\n"
                "Harmodios\n"
                "Citoyen athénien connu avec Aristogiton comme symbole de lutte contre la tyrannie.\n\n"
                "G038\n"
                "Héraclite\n"
                "Philosophe présocratique grec, connu pour sa doctrine du changement.\n\n"
                "6. Termes grecs traduits en français\n\n"
                "T001\n"
                "ὑπομνήματα\n"
                "Traduction française à utiliser dans le texte : notes personnelles.\n"
                "Sens : notes, souvenirs ou mémoranda destinés à soi-même.\n\n"
                "7. Variantes éditoriales à surveiller\n\n"
                "V003\n"
                "Platon / Plato\n"
                "Règle : utiliser Platon en français.\n"
            ),
            encoding="utf-8",
        )

        merged_text = kdp_mode.build_merged_kdp_source(fr_edition).read_text(encoding="utf-8")

        self.assertIn("# GLOSSAIRE", merged_text)
        self.assertRegex(
            merged_text,
            r'<span id="glossary-term-\d+"></span>G037 - Harmodios - '
            r"Citoyen athénien connu avec Aristogiton comme symbole de lutte contre la tyrannie\.",
        )
        self.assertIn("1\\. Philosophes, auteurs, maîtres et personnages grecs", merged_text)
        self.assertIn(
            "G038 - Héraclite - Philosophe présocratique grec, connu pour sa doctrine du changement.",
            merged_text,
        )
        self.assertIn("6\\. Termes grecs traduits en français", merged_text)
        self.assertIn(
            "T001 - ὑπομνήματα - Traduction française à utiliser dans le texte : notes personnelles. "
            "Sens : notes, souvenirs ou mémoranda destinés à soi-même.",
            merged_text,
        )
        self.assertIn("7\\. Variantes éditoriales à surveiller", merged_text)
        self.assertIn("V003 - Platon / Plato - Règle : utiliser Platon en français.", merged_text)
        self.assertNotIn("**G038", merged_text)
        self.assertNotIn("    G037", merged_text)

    def test_build_kdp_respects_explicit_preface_heading_before_first_chapter(self):
        from editorial import kdp_mode

        self.pre_edition_path.write_text(
            (
                "Preface\n\n"
                "[Author preface to be inserted in final editorial pass.]\n\n"
                "# Chapter 01 - First Case\n\n"
                "Body text.\n"
            ),
            encoding="utf-8",
        )

        merged_text = kdp_mode.build_merged_kdp_source(self.edition).read_text(encoding="utf-8")

        self.assertIn("# Preface", merged_text)
        self.assertIn("[Author preface to be inserted in final editorial pass.]", merged_text)
        self.assertNotIn("# Adapted Preface", merged_text)
        self.assertIn("# Chapter 01 - First Case", merged_text)

    def test_build_kdp_keeps_numeric_chapter_heading_without_visual_title_duplication(self):
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

        self.assertIn("# Chapter 01 - Death Strikes a King", merged_text)
        chapter_idx = merged_text.index("# Chapter 01 - Death Strikes a King")
        image_idx = merged_text.index("![](assets/images/ch01_01.jpg)")
        body_idx = merged_text.index("The king of Vendhya was dying.")
        self.assertLess(chapter_idx, image_idx)
        self.assertLess(image_idx, body_idx)
        self.assertNotIn("**Chapter 01 - Death Strikes a King**", merged_text)

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

        self.assertEqual(first.count("**Chapter 01 - Death Strikes a King**"), 0)
        self.assertEqual(second.count("**Chapter 01 - Death Strikes a King**"), 0)

    def test_build_kdp_removes_duplicate_visual_title_after_heading_and_image(self):
        from editorial import kdp_mode

        self.pre_edition_path.write_text(
            (
                "# Chapter 04 - The Horror at the Camp\n\n"
                "![](assets/images/ch04_01.jpg)\n\n"
                "**Chapter 04 - The Horror at the Camp**\n\n"
                "The camp was no longer as we had left it.\n"
            ),
            encoding="utf-8",
        )

        merged_text = kdp_mode.build_merged_kdp_source(self.edition).read_text(encoding="utf-8")

        self.assertIn("# Chapter 04 - The Horror at the Camp", merged_text)
        self.assertIn("![](assets/images/ch04_01.jpg)", merged_text)
        self.assertIn("The camp was no longer as we had left it.", merged_text)
        self.assertNotIn("**Chapter 04 - The Horror at the Camp**", merged_text)

    def test_build_kdp_does_not_promote_roman_sentence_to_chapter_heading(self):
        from editorial import kdp_mode

        self.pre_edition_path.write_text(
            (
                "# Chapter 01 - The Warning from Miskatonic\n\n"
                "Opening body.\n\n"
                "## By Author Marker Contract\n\n"
                "\\newpage\n\n"
                "# Chapter 04 - The Horror at the Camp\n\n"
                "I have already told of the ruined camp and the missing dog.\n"
            ),
            encoding="utf-8",
        )

        merged_text = kdp_mode.build_merged_kdp_source(self.edition).read_text(encoding="utf-8")

        self.assertIn("# Chapter 01 - The Warning from Miskatonic", merged_text)
        self.assertIn("# Chapter 04 - The Horror at the Camp", merged_text)
        self.assertNotIn("# chapter 01 - have already told", merged_text.casefold())
        self.assertNotIn("**chapter 01 - have already told", merged_text.casefold())

    def test_build_kdp_strips_trailing_author_byline_from_preface(self):
        from editorial import kdp_mode

        self.pre_edition_path.write_text(
            (
                "# Marker Contract Book\n\n"
                "This is the real prefatory paragraph that should remain.\n\n"
                "## By Author Marker Contract\n\n"
                "\\newpage\n\n"
                "# Chapter 01 - First Case\n\n"
                "Body text.\n"
            ),
            encoding="utf-8",
        )

        merged_text = kdp_mode.build_merged_kdp_source(self.edition).read_text(encoding="utf-8")

        self.assertIn("# Adapted Preface", merged_text)
        self.assertIn("This is the real prefatory paragraph that should remain.", merged_text)
        self.assertNotIn("By Author Marker Contract", merged_text)
        self.assertIn("# Chapter 01 - First Case", merged_text)

    def test_build_kdp_uses_fixed_editorial_block_order_and_appends_epilogue_last(self):
        from editorial import kdp_mode

        BookEditionTemplate.objects.create(
            book_code=self.work.code,
            language="en",
            title=self.work.title,
            author_name=self.author.name,
            publication_year=2026,
            frontispiece_text="Front page",
            copyright_text="Rights page",
            about_edition_text="About this book block",
            has_preface=True,
            preface_text="Preface block",
            has_introduction=True,
            introduction_text="Introduction block",
            has_epilogue=True,
            epilogue_text="Epilogue block",
        )
        self.edition.frontispiece_template = "Front page"
        self.edition.copyright_template = "Rights page"
        self.edition.about_edition_template = "About this book block"
        self.edition.save(
            update_fields=["frontispiece_template", "copyright_template", "about_edition_template"]
        )
        kdp_mode.build_frontmatter_files(self.edition, Path("data") / "frontmatter")
        self.pre_edition_path.write_text("# Chapter 01 - First Case\n\nBody text.\n", encoding="utf-8")

        merged_text = kdp_mode.build_merged_kdp_source(self.edition).read_text(encoding="utf-8")

        front_idx = merged_text.index("# Frontispiece")
        copyright_idx = merged_text.index("# Copyright")
        about_idx = merged_text.index("# About This Book")
        preface_idx = merged_text.index("# Preface")
        intro_idx = merged_text.index("# Introduction")
        chapter_idx = merged_text.index("# Chapter 01 - First Case")
        epilogue_idx = merged_text.index("# Epilogue")

        self.assertLess(front_idx, copyright_idx)
        self.assertLess(copyright_idx, about_idx)
        self.assertLess(about_idx, preface_idx)
        self.assertLess(preface_idx, intro_idx)
        self.assertLess(intro_idx, chapter_idx)
        self.assertLess(chapter_idx, epilogue_idx)

    def test_build_frontmatter_skips_empty_optional_blocks_even_when_flagged(self):
        from editorial.frontmatter import build_frontmatter_files, optional_section_warnings

        template = BookEditionTemplate.objects.create(
            book_code=self.work.code,
            language="en",
            title=self.work.title,
            author_name=self.author.name,
            publication_year=2026,
            frontispiece_text="Front page",
            copyright_text="Rights page",
            about_edition_text="About this book block",
            has_preface=True,
            preface_text="",
            has_introduction=False,
            introduction_text="",
            has_epilogue=True,
            epilogue_text="   ",
        )
        self.edition.frontispiece_template = "Front page"
        self.edition.copyright_template = "Rights page"
        self.edition.about_edition_template = "About this book block"
        self.edition.save(
            update_fields=["frontispiece_template", "copyright_template", "about_edition_template"]
        )

        build_frontmatter_files(self.edition, Path("data") / "frontmatter")
        front_dir = Path("data") / "frontmatter" / self.work.code / "en"

        self.assertEqual((front_dir / "preface.md").read_text(encoding="utf-8"), "")
        self.assertEqual((front_dir / "epilogue.md").read_text(encoding="utf-8"), "")
        warnings = optional_section_warnings(template, "en")
        self.assertEqual(len(warnings), 2)


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
