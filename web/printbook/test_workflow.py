from types import SimpleNamespace

from django.test import SimpleTestCase

from gaiden.print_engine.manuscript import manuscript_tex
from web.printbook.forms import InteriorSettingsForm
from web.printbook.services import official_composition_preset, spec_for


class PrintBookWorkflowTests(SimpleTestCase):
    def _edition(self, **overrides):
        values = {
            "platform": "ingramspark",
            "binding": "paperback",
            "paper": "cream",
            "trim": "6x9",
            "interior_color": "black_and_white",
            "composition_settings": {},
            "typography": SimpleNamespace(font="TeX Gyre Pagella", size_pt=11, leading_pt=14),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_uiclap_rejects_unavailable_hardcover_binding(self):
        with self.assertRaisesMessage(ValueError, "encadernação escolhida não é compatível"):
            spec_for(self._edition(platform="uiclap", binding="hardcover"))

    def test_interior_settings_require_leading_not_smaller_than_font(self):
        form = InteriorSettingsForm(data={
            "body_font": "TeX Gyre Pagella", "font_size_pt": "12", "leading_pt": "10",
            "paragraph_indent_em": "1.25", "inner_margin_in": "0.75", "outer_margin_in": "0.5",
            "top_margin_in": "0.55", "bottom_margin_in": "0.65", "header_style": "none",
            "footer_style": "page", "page_numbering": "on", "chapter_opening": "recto",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("entrelinha não pode ser menor", form.errors["leading_pt"][0])

    def test_print_composition_changes_only_generated_latex(self):
        spec = spec_for(self._edition(composition_settings={
            "paragraph_indent_em": "0.8", "header_style": "chapter", "footer_style": "none",
            "page_numbering": False, "chapter_opening": "inline",
        }))
        source = "# Chapter 1\n\nA Beginning\n\nThe approved prose remains intact."
        rendered = manuscript_tex(source, spec, composition={
            "paragraph_indent_em": "0.8", "header_style": "chapter", "footer_style": "none",
            "page_numbering": False, "chapter_opening": "inline",
        })
        self.assertIn(r"\setlength{\parindent}{0.8em}", rendered)
        self.assertIn("The approved prose remains intact.", rendered)
        self.assertNotIn(r"\makeoddfoot{gaiden}{}{\small\thepage}{}", rendered)

    def test_illustrated_chapter_keeps_heading_and_first_paragraph_as_body(self):
        rendered = manuscript_tex(
            "# Chapter 03\n\nThe first paragraph remains ordinary body text.\n\nSecond paragraph.",
            spec_for(self._edition()),
            image_manifest={3: "illustrations/FIGURE_003.jpg"},
            composition={"chapter_opening": "recto"},
        )

        self.assertIn(r"\illustrationpage{Chapter 03}{illustrations/FIGURE_003.jpg}", rendered)
        self.assertIn(r"\cleardoublepage\thispagestyle{empty}", rendered)
        self.assertIn("The first paragraph remains ordinary body text.", rendered)
        self.assertNotIn(r"\chaptertitle{The first paragraph", rendered)

    def test_ingramspark_preset_uses_documented_half_inch_minimum(self):
        preset = official_composition_preset(self._edition(platform="ingramspark"))
        self.assertEqual(preset["values"]["inner_margin_in"], "0.50")
        self.assertEqual(preset["values"]["outer_margin_in"], "0.50")

    def test_kdp_preset_tracks_official_page_count_bands(self):
        edition = self._edition(platform="amazon_kdp")
        expected = {100: "0.375", 200: "0.50", 400: "0.625", 600: "0.75", 800: "0.875"}
        for pages, gutter in expected.items():
            with self.subTest(pages=pages):
                self.assertEqual(official_composition_preset(edition, pages)["values"]["inner_margin_in"], gutter)
        self.assertEqual(official_composition_preset(edition)["values"]["inner_margin_in"], "0.875")

    def test_uiclap_preset_converts_documented_centimeter_margins(self):
        preset = official_composition_preset(self._edition(platform="uiclap"))
        self.assertEqual(preset["values"]["inner_margin_in"], "0.80")
        self.assertEqual(preset["values"]["outer_margin_in"], "0.60")
