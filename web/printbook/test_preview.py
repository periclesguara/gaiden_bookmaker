from types import SimpleNamespace
from unittest.mock import patch
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase

from web.printbook.forms import CoverDesignForm
from web.printbook.services import estimated_spine_width, save_cover_draft_fields, sha, verified_build_file
from web.printbook.views import action, preview, preview_navigation


class PreviewNavigationTests(SimpleTestCase):
    def test_saved_cover_image_remains_readable_from_legacy_data_root(self):
        with TemporaryDirectory() as temporary:
            repository = Path(temporary)
            path = repository / "data/printbook/6/coverbuild/v0023/COVER_IMAGE.png"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"saved-cover-image")
            build = SimpleNamespace(
                edition_id=6,
                version=23,
                _meta=SimpleNamespace(model_name="coverbuild"),
            )
            item = {"path": str(path), "sha256": sha(path)}
            with patch("web.printbook.services.storage.repo_root", return_value=repository), patch(
                "web.printbook.services.storage.storage_root", return_value=repository / "external"
            ):
                self.assertEqual(verified_build_file(build, item), path.resolve())

    def test_single_page_mode_reaches_every_page(self):
        for page in range(1, 7):
            navigation = preview_navigation("interior", page, 6, "single")
            self.assertEqual(navigation["numbers"], [page])
            self.assertEqual(navigation["previous"], page - 1 if page > 1 else None)
            self.assertEqual(navigation["next"], page + 1 if page < 6 else None)

    def test_spreads_follow_bound_book_order(self):
        for page, expected in ((1, [None, 1]), (2, [2, 3]), (3, [2, 3]), (6, [6, None])):
            with self.subTest(page=page):
                navigation = preview_navigation("interior", page, 6, "spread")
                self.assertEqual(navigation["numbers"], expected)
        self.assertEqual(preview_navigation("interior", 1, 6, "spread")["next"], 2)
        self.assertEqual(preview_navigation("interior", 2, 6, "spread")["previous"], 1)
        self.assertIsNone(preview_navigation("interior", 6, 6, "spread")["next"])

    def test_cover_is_always_a_single_full_wrap(self):
        navigation = preview_navigation("cover", 1, 1, "spread")
        self.assertEqual(navigation["mode"], "single")
        self.assertEqual(navigation["numbers"], [1])
        self.assertIsNone(navigation["next"])

    def test_unknown_mode_defaults_to_spread(self):
        self.assertEqual(preview_navigation("interior", 1, 2, "invalid")["mode"], "spread")

    def test_rendered_single_mode_keeps_next_page_and_mode(self):
        request = RequestFactory().get("/printbook/preview/interior/1/", {"mode": "single", "page": "2"})
        build = SimpleNamespace(pk=1, version=1, edition_id=1)
        with patch("web.printbook.views.get_object_or_404", return_value=build), patch(
            "web.printbook.views.artifact", return_value="unused.pdf"
        ), patch("web.printbook.views.services.inspect_pdf_page_count", return_value=6), patch(
            "web.printbook.views.preview_panels", return_value=[{"number": 2}]
        ):
            response = preview(request, "interior", 1)
        self.assertContains(response, '?page=3&amp;mode=single')
        self.assertContains(response, 'class="spread single"')
        self.assertContains(response, 'alt="Página 2"')
        self.assertNotContains(response, 'alt="Página 3"')
        self.assertContains(response, "Página principal")
        self.assertContains(response, "Voltar para a edição")


class EpubMemberTests(SimpleTestCase):
    def test_member_paths_remain_inside_the_epub(self):
        from web.printbook.epub_preview import member_path
        self.assertEqual(member_path("EPUB/text", "../style.css"), "EPUB/style.css")
        for href in ("../../../private", "/etc/passwd", "https://example.com/book", "%2e%2e/%2e%2e/private", "..\\private"):
            with self.subTest(href=href), self.assertRaises(ValueError):
                member_path("EPUB", href)

    def test_xml_does_not_expand_entities(self):
        from web.printbook.epub_preview import parse_xml
        root = parse_xml(b'<!DOCTYPE root [<!ENTITY example "expanded">]><root>&example;</root>')
        self.assertIsNone(root.text)


class CoverStudioFormTests(SimpleTestCase):
    def _data(self):
        return {
            "layout": "classic",
            "save_mode": "draft",
            "enhancement_profile": "print_safe",
            "title": "A Book",
            "subtitle": "",
            "author": "An Author",
            "back_text": "Back copy",
            "back_heading": "About",
            "back_quote": "A review",
            "back_bio": "Author bio",
            "spine_text": "A Book",
            "image_fit": "cover",
            "image_zoom": "100",
            "image_x": "50",
            "image_y": "50",
            "overlay_opacity": "25",
            "background_color": "#111827",
            "text_color": "#FFFFFF",
            "accent_color": "#D4A24C",
            "back_palette": "midnight",
            "back_background_color": "#1F2937",
            "back_text_color": "#FFFFFF",
            "back_accent_color": "#D4A24C",
            "spine": "0.4275",
        }

    def test_visual_proof_does_not_require_official_template(self):
        form = CoverDesignForm(data=self._data())
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["layout"], "art_only")
        self.assertEqual(form.cleaned_data["overlay_opacity"], 0)

    def test_draft_save_redirects_to_the_requested_next_cover_stage(self):
        data = self._data()
        data["continue_to"] = "back"
        request = RequestFactory().post("/printbook/6/action/design_cover/", data)
        edition = SimpleNamespace(pk=6, cover_settings={})

        with patch("web.printbook.views.get_object_or_404", return_value=edition), patch(
            "web.printbook.views.services.save_cover_draft_fields"
        ) as save_fields, patch("web.printbook.views.services.design_cover") as design_cover, patch(
            "web.printbook.views.messages.success"
        ):
            response = action(request, 6, "design_cover")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].endswith("/printbook/6/cover/?tab=back&saved=draft"))
        save_fields.assert_called_once()
        self.assertEqual(design_cover.call_args.kwargs["save_mode"], "draft")

    def test_front_stage_can_save_in_place_without_hidden_stage_reset(self):
        data = self._data()
        data.update({
            "continue_to": "front",
            "front_title_x_percent": "63",
            "back_element_heading_y_percent": "999",
        })
        request = RequestFactory().post("/printbook/6/action/design_cover/", data)
        edition = SimpleNamespace(pk=6, cover_settings={"back_element_heading_y_percent": 12})

        with patch("web.printbook.views.get_object_or_404", return_value=edition), patch(
            "web.printbook.views.services.save_cover_draft_fields"
        ) as save_fields, patch("web.printbook.views.services.design_cover") as design_cover, patch(
            "web.printbook.views.messages.success"
        ):
            response = action(request, 6, "design_cover")

        self.assertTrue(response["Location"].endswith("/printbook/6/cover/?tab=front&saved=draft"))
        self.assertEqual(save_fields.call_args.args[1]["front_title_x_percent"], 63)
        self.assertEqual(save_fields.call_args.args[1]["back_element_heading_y_percent"], 12)
        design_cover.assert_called_once()

    def test_front_stage_exposes_separate_save_and_follow_actions(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text(encoding="utf-8")

        self.assertIn('data-save-next="front"', template)
        self.assertIn('>Salvar capa</button>', template)
        self.assertIn('data-save-next="back"', template)
        self.assertIn('>Salvar e seguir para Contracapa →</button>', template)
        self.assertIn("window.addEventListener('beforeunload'", template)

    def test_back_stage_can_save_in_place_without_hidden_spine_reset(self):
        data = self._data()
        data.update({
            "current_stage": "back",
            "continue_to": "back",
            "back_element_body_y_percent": "44",
            "spine_edge_safe_mm": "1.5",
        })
        request = RequestFactory().post("/printbook/6/action/design_cover/", data)
        edition = SimpleNamespace(pk=6, cover_settings={"spine_edge_safe_mm": "1.6"})

        with patch("web.printbook.views.get_object_or_404", return_value=edition), patch(
            "web.printbook.views.services.save_cover_draft_fields"
        ) as save_fields, patch("web.printbook.views.services.design_cover") as design_cover, patch(
            "web.printbook.views.messages.success"
        ):
            response = action(request, 6, "design_cover")

        self.assertTrue(response["Location"].endswith("/printbook/6/cover/?tab=back&saved=draft"))
        self.assertEqual(save_fields.call_args.args[1]["back_element_body_y_percent"], 44)
        self.assertEqual(save_fields.call_args.args[1]["spine_edge_safe_mm"], Decimal("1.6"))
        design_cover.assert_called_once()

    def test_back_stage_exposes_separate_save_and_follow_actions(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text(encoding="utf-8")

        self.assertIn('name="current_stage" value="front"', template)
        self.assertIn('>Salvar contracapa</button>', template)
        self.assertIn('>Salvar e seguir para Lombada →</button>', template)

    def test_spine_stage_can_save_in_place(self):
        data = self._data()
        data.update({
            "current_stage": "spine",
            "continue_to": "spine",
            "spine_title": "A Book",
            "spine_rotation": "-90",
        })
        request = RequestFactory().post("/printbook/6/action/design_cover/", data)
        edition = SimpleNamespace(pk=6, cover_settings={})

        with patch("web.printbook.views.get_object_or_404", return_value=edition), patch(
            "web.printbook.views.services.save_cover_draft_fields"
        ) as save_fields, patch("web.printbook.views.services.design_cover") as design_cover, patch(
            "web.printbook.views.messages.success"
        ):
            response = action(request, 6, "design_cover")

        self.assertTrue(response["Location"].endswith("/printbook/6/cover/?tab=spine&saved=draft"))
        self.assertEqual(save_fields.call_args.args[1]["spine_title"], "A Book")
        self.assertEqual(save_fields.call_args.args[1]["spine_rotation"], "-90")
        design_cover.assert_called_once()

    def test_spine_stage_keeps_controls_and_adds_save_actions(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text(encoding="utf-8")

        self.assertIn("{{ design_form.spine_image.label_tag }}", template)
        self.assertIn("{{ design_form.spine_title.label_tag }}", template)
        self.assertIn("{{ design_form.spine_author.label_tag }}", template)
        self.assertIn('>Salvar lombada</button>', template)
        self.assertIn('>Salvar e seguir para Montagem →</button>', template)
        self.assertIn("Você ainda pode ajustar e salvar esta etapa", template)
        self.assertNotIn('data-next-tab="montage">Continuar automaticamente para Montagem →', template)
        self.assertIn("simulator.dataset.noSpine === 'yes'", template)

    def test_montage_save_action_remains_available_for_stapled_proofs(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text(encoding="utf-8")

        self.assertIn(
            'name="save_mode" value="draft" data-save-montage {% if not interior or source_error %}disabled{% endif %}>Salvar montagem da capa</button>',
            template,
        )
        self.assertIn("event.submitter.textContent = 'Salvando montagem…'", template)
        self.assertIn('<p data-montage-save-status role="status">', template)
        self.assertIn(
            'name="save_mode" value="final" class="button-final" {% if not interior or source_error or stapled_uiclap %}disabled{% endif %}>Salvar capa final</button>',
            template,
        )

    def test_automatic_cover_pdf_goes_directly_to_preflight(self):
        upload = SimpleUploadedFile("configured-cover.pdf", b"%PDF-1.4\n", content_type="application/pdf")
        request = RequestFactory().post("/printbook/6/action/automatic_cover_pdf/", {"cover_pdf": upload})
        edition = SimpleNamespace(pk=6)
        with patch("web.printbook.views.get_object_or_404", return_value=edition), patch(
            "web.printbook.views.services.import_automatic_cover_pdf"
        ) as importer, patch("web.printbook.views.messages.success"):
            response = action(request, 6, "automatic_cover_pdf")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].endswith("/printbook/6/preflight/"))
        importer.assert_called_once()
        imported_edition, imported_upload = importer.call_args.args
        self.assertIs(imported_edition, edition)
        self.assertEqual(imported_upload.name, "configured-cover.pdf")
        self.assertEqual(imported_upload.content_type, "application/pdf")

    def test_front_and_back_text_fields_may_be_saved_empty(self):
        data = self._data()
        data.update({
            "save_mode": "final",
            "title": "",
            "subtitle": "",
            "author": "",
            "language": "",
            "publisher": "",
            "back_heading": "",
            "back_quote": "",
            "back_text": "",
            "back_bio": "",
            "back_footer": "",
        })
        form = CoverDesignForm(data=data)
        self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_cover_template_has_delete_action_for_each_optional_copy_field(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text(encoding="utf-8")
        for field in (
            "title", "subtitle", "author", "language", "publisher",
            "back_heading", "back_quote", "back_text", "back_bio", "back_footer",
        ):
            self.assertIn(f'data-clear-field="{field}"', template)
        self.assertNotIn("value('back_text') || 'Texto da contracapa'", template)
        self.assertNotIn("value('back_heading') || 'Sobre este livro'", template)

    def test_montage_save_returns_to_montage(self):
        data = self._data()
        data["continue_to"] = "montage"
        request = RequestFactory().post("/printbook/6/action/design_cover/", data)
        edition = SimpleNamespace(pk=6, cover_settings={})

        with patch("web.printbook.views.get_object_or_404", return_value=edition), patch(
            "web.printbook.views.services.save_cover_draft_fields"
        ), patch("web.printbook.views.services.design_cover"), patch(
            "web.printbook.views.messages.success"
        ):
            response = action(request, 6, "design_cover")

        self.assertTrue(response["Location"].endswith("/printbook/6/cover/?tab=montage&saved=draft"))

    def test_hidden_future_stage_error_does_not_block_front_cover_save(self):
        data = self._data()
        data.update({"continue_to": "back", "spine_edge_safe_mm": "1.5"})
        request = RequestFactory().post("/printbook/6/action/design_cover/", data)
        edition = SimpleNamespace(pk=6, cover_settings={})

        with patch("web.printbook.views.get_object_or_404", return_value=edition), patch(
            "web.printbook.views.services.save_cover_draft_fields"
        ) as save_fields, patch("web.printbook.views.services.design_cover") as design_cover, patch(
            "web.printbook.views.messages.success"
        ):
            response = action(request, 6, "design_cover")

        self.assertTrue(response["Location"].endswith("/printbook/6/cover/?tab=back&saved=draft"))
        self.assertEqual(save_fields.call_args.args[1]["spine_edge_safe_mm"], Decimal("1.6"))
        design_cover.assert_called_once()

    def test_invalid_current_field_preserves_every_other_valid_field(self):
        data = self._data()
        data.update({
            "continue_to": "back",
            "add_front_text": "on",
            "title": "",
            "author": "Epicteto de Hierápolis",
            "front_title_x_percent": "63",
            "minimum_dpi": "20",
        })
        request = RequestFactory().post("/printbook/6/action/design_cover/", data)
        edition = SimpleNamespace(pk=6, cover_settings={})

        with patch("web.printbook.views.get_object_or_404", return_value=edition), patch(
            "web.printbook.views.services.save_cover_draft_fields"
        ) as save_fields, patch("web.printbook.views.services.design_cover") as design_cover, patch(
            "web.printbook.views.messages.error"
        ), patch("web.printbook.views.messages.warning"):
            response = action(request, 6, "design_cover")

        self.assertTrue(response["Location"].endswith("/printbook/6/cover/?tab=front&saved=partial"))
        self.assertEqual(save_fields.call_args.args[1]["front_title_x_percent"], 63)
        self.assertEqual(save_fields.call_args.args[1]["author"], "Epicteto de Hierápolis")
        design_cover.assert_not_called()

    def test_cover_draft_fields_are_persisted_before_compilation(self):
        class DraftEdition:
            cover_settings = {"title": "Versão anterior"}
            saved_fields = None

            def save(self, *, update_fields):
                self.saved_fields = update_fields

        edition = DraftEdition()
        draft = save_cover_draft_fields(edition, {
            "title": "Manual de Epicteto",
            "front_title_x_percent": 51,
            "front_title_font_size_pt": Decimal("28.5"),
            "front_title_color": "#EAB308",
            "add_front_text": True,
            "cover_image": object(),
        })

        self.assertEqual(draft["title"], "Manual de Epicteto")
        self.assertEqual(draft["front_title_x_percent"], 51)
        self.assertEqual(draft["front_title_font_size_pt"], "28.5")
        self.assertEqual(draft["front_title_color"], "#EAB308")
        self.assertTrue(draft["add_front_text"])
        self.assertNotIn("cover_image", draft)
        self.assertEqual(edition.saved_fields, ["cover_settings", "updated_at"])

    def test_saving_an_empty_field_replaces_the_previous_text(self):
        class DraftEdition:
            cover_settings = {"title": "Texto antigo", "back_text": "Sinopse antiga"}

            def save(self, *, update_fields):
                self.saved_fields = update_fields

        edition = DraftEdition()
        draft = save_cover_draft_fields(edition, {"title": "", "back_text": ""})

        self.assertEqual(draft["title"], "")
        self.assertEqual(draft["back_text"], "")

    def test_cover_font_catalog_uses_professional_editorial_families(self):
        values = dict(CoverDesignForm.COVER_FONTS)

        self.assertIn("EB Garamond", values)
        self.assertIn("GFS Didot", values)
        self.assertIn("GFS BodoniClassic", values)
        self.assertNotIn("Arial", values)
        self.assertNotIn("Times New Roman", values)
        for field_name in (
            "front_title_font_size_pt", "front_title_style", "front_title_letter_spacing_pt",
            "front_subtitle_font_size_pt", "front_author_font_size_pt", "front_language_font_size_pt",
            "front_language_style", "front_language_x_percent", "front_publisher_font_size_pt",
        ):
            self.assertIn(field_name, CoverDesignForm.base_fields)

    def test_front_text_fields_may_be_individually_empty_when_enabled(self):
        data = self._data()
        data.update({"add_front_text": "on", "title": "", "author": ""})
        form = CoverDesignForm(data=data)

        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.cleaned_data["title"], "")
        self.assertEqual(form.cleaned_data["author"], "")

    def test_draft_form_keeps_selected_production_profile(self):
        data = self._data()
        data["enhancement_profile"] = "production_300"
        form = CoverDesignForm(data=data)

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["enhancement_profile"], "production_300")

    def test_final_cover_may_explicitly_approve_an_empty_back_cover(self):
        data = self._data()
        data.update({
            "save_mode": "final",
            "back_heading": "",
            "back_text": "",
            "approve_empty_back": "on",
        })

        form = CoverDesignForm(data=data)

        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertTrue(form.cleaned_data["approve_empty_back"])

    def test_image_in_template_field_gets_specific_error(self):
        upload = SimpleUploadedFile("cover.jpg", b"not-a-pdf", content_type="image/jpeg")
        form = CoverDesignForm(data=self._data(), files={"template": upload})

        self.assertFalse(form.is_valid())
        self.assertIn("Use o primeiro campo para a imagem da capa", form.errors["template"][0])

    def test_simulator_spine_estimate_is_bound_to_paper_and_page_count(self):
        self.assertEqual(estimated_spine_width(SimpleNamespace(paper="white"), 190), Decimal("0.4275"))
