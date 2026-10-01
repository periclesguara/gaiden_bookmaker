from types import SimpleNamespace
from unittest.mock import patch
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase

from web.printbook.forms import CoverDesignForm
from web.printbook.services import estimated_spine_width
from web.printbook.views import preview, preview_navigation


class PreviewNavigationTests(SimpleTestCase):
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

    def test_front_text_is_required_only_when_operator_enables_it(self):
        data = self._data()
        data.update({"add_front_text": "on", "title": "", "author": ""})
        form = CoverDesignForm(data=data)

        self.assertFalse(form.is_valid())
        self.assertIn("title", form.errors)
        self.assertIn("author", form.errors)

    def test_draft_form_keeps_selected_production_profile(self):
        data = self._data()
        data["enhancement_profile"] = "production_300"
        form = CoverDesignForm(data=data)

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["enhancement_profile"], "production_300")

    def test_image_in_template_field_gets_specific_error(self):
        upload = SimpleUploadedFile("cover.jpg", b"not-a-pdf", content_type="image/jpeg")
        form = CoverDesignForm(data=self._data(), files={"template": upload})

        self.assertFalse(form.is_valid())
        self.assertIn("Use o primeiro campo para a imagem da capa", form.errors["template"][0])

    def test_simulator_spine_estimate_is_bound_to_paper_and_page_count(self):
        self.assertEqual(estimated_spine_width(SimpleNamespace(paper="white"), 190), Decimal("0.4275"))
