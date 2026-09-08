from __future__ import annotations

from decimal import Decimal
from unittest import TestCase

from gaiden.print_engine.cover import CoverLayout
from gaiden.print_engine.geometry import CoverGeometry
from gaiden.print_engine.latex import BookContent, Chapter, LatexInteriorRenderer
from gaiden.print_engine.package import PrintPackageManifest
from gaiden.print_engine.preflight import PreflightStatus, preflight_spec
from gaiden.print_engine.providers.ingramspark import IngramSparkAdapter
from gaiden.print_engine.specs import PrintSpec


class PrintEngineContractTests(TestCase):
    def final_spec(self, *, pages: int = 286, spine: str = "0.600") -> PrintSpec:
        return PrintSpec().finalize(page_count=pages, spine_width_in=spine)

    def test_cover_geometry_matches_finalized_6x9_interior(self):
        geometry = CoverGeometry.from_spec(self.final_spec())

        self.assertEqual(geometry.spread_width_in, Decimal("12.850"))
        self.assertEqual(geometry.spread_height_in, Decimal("9.250"))
        self.assertEqual(geometry.page_count, 286)
        self.assertTrue(geometry.spine_text_allowed)

    def test_cover_layout_places_back_spine_and_front_on_same_canvas(self):
        layout = CoverLayout.from_spec(self.final_spec())

        self.assertEqual(layout.back.x, Decimal("0.125"))
        self.assertEqual(layout.spine.x, Decimal("6.125"))
        self.assertEqual(layout.front.x, Decimal("6.725"))
        self.assertEqual(layout.canvas.width, Decimal("12.850"))
        self.assertEqual(layout.front_safe.x, Decimal("6.975"))

    def test_cover_fingerprint_changes_with_final_interior_geometry(self):
        first = CoverGeometry.from_spec(self.final_spec(pages=286, spine="0.600"))
        second = CoverGeometry.from_spec(self.final_spec(pages=302, spine="0.640"))

        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_paperback_under_48_pages_disables_spine_text(self):
        geometry = CoverGeometry.from_spec(self.final_spec(pages=40, spine="0.100"))

        self.assertFalse(geometry.spine_text_allowed)

    def test_ingram_adapter_refuses_unfinalized_cover_geometry(self):
        errors = IngramSparkAdapter.validate_spec(PrintSpec())

        self.assertTrue(any("page_count" in error for error in errors))
        self.assertTrue(any("spine_width_in" in error for error in errors))

    def test_ingram_adapter_rejects_margin_inside_safe_area(self):
        spec = PrintSpec(outer_margin_in=Decimal("0.4")).finalize(
            page_count=200,
            spine_width_in="0.5",
        )

        errors = IngramSparkAdapter.validate_spec(spec)

        self.assertTrue(any("outer_margin_in" in error for error in errors))

    def test_preflight_is_no_go_for_unsupported_bleed_interior(self):
        spec = PrintSpec(interior_bleed=True).finalize(page_count=200, spine_width_in="0.5")

        report = preflight_spec(spec)

        self.assertFalse(report.passed)
        self.assertTrue(any(check.status == PreflightStatus.FAIL for check in report.checks))
        self.assertTrue(any("bleed" in check.message.lower() for check in report.checks))

    def test_latex_renderer_uses_mirrored_margins_and_escapes_metadata(self):
        content = BookContent.from_chapters(
            title="Reason & Ethics",
            subtitle="A 100% Practical Edition",
            author="Rino_Books",
            chapters=(Chapter("Chapter #1", "First paragraph.\n\nSecond paragraph."),),
        )

        tex = LatexInteriorRenderer().render(content, PrintSpec())

        self.assertIn(r"\documentclass[11pt,twoside,openright]{memoir}", tex)
        self.assertIn(r"paperwidth=6in,paperheight=9in", tex)
        self.assertIn(r"inner=0.75in,outer=0.5in,top=0.55in,bottom=0.65in", tex)
        self.assertIn(r"Reason \& Ethics", tex)
        self.assertIn(r"A 100\% Practical Edition", tex)
        self.assertIn(r"Rino\_Books", tex)
        self.assertIn(r"\chapter{Chapter \#1}", tex)
        self.assertIn(r"\widowpenalty=10000", tex)

    def test_latex_renderer_refuses_bleed_interior_in_v1(self):
        content = BookContent.from_chapters(
            title="Book",
            author="Author",
            chapters=(Chapter("One", "Text"),),
        )

        with self.assertRaisesRegex(ValueError, "bleed"):
            LatexInteriorRenderer().render(content, PrintSpec(interior_bleed=True))

    def test_manifest_binds_cover_to_geometry_fingerprint(self):
        spec = self.final_spec()
        geometry = IngramSparkAdapter.cover_geometry(spec)
        manifest = PrintPackageManifest(book_id="book_7003", spec=spec, geometry=geometry)

        payload = manifest.as_dict()

        self.assertEqual(payload["book_id"], "book_7003")
        self.assertEqual(payload["page_count"], 286)
        self.assertEqual(payload["artifacts"], {"interior": "INTERIOR.pdf", "cover": "COVER.pdf"})
        self.assertEqual(payload["cover"]["geometry_fingerprint"], geometry.fingerprint)
