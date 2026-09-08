from __future__ import annotations

from decimal import Decimal

import pytest

from gaiden.print_engine.geometry import CoverGeometry
from gaiden.print_engine.latex import BookContent, Chapter, LatexInteriorRenderer
from gaiden.print_engine.package import PrintPackageManifest
from gaiden.print_engine.preflight import PreflightStatus, preflight_spec
from gaiden.print_engine.providers.ingramspark import IngramSparkAdapter
from gaiden.print_engine.specs import Binding, PrintSpec


def _final_spec(*, pages: int = 286, spine: str = "0.600") -> PrintSpec:
    return PrintSpec().finalize(page_count=pages, spine_width_in=spine)


def test_cover_geometry_matches_finalized_6x9_interior():
    geometry = CoverGeometry.from_spec(_final_spec())

    assert geometry.spread_width_in == Decimal("12.850")
    assert geometry.spread_height_in == Decimal("9.250")
    assert geometry.page_count == 286
    assert geometry.spine_text_allowed is True


def test_cover_fingerprint_changes_when_final_page_count_changes():
    first = CoverGeometry.from_spec(_final_spec(pages=286, spine="0.600"))
    second = CoverGeometry.from_spec(_final_spec(pages=302, spine="0.640"))

    assert first.fingerprint != second.fingerprint


def test_paperback_under_48_pages_disables_spine_text():
    geometry = CoverGeometry.from_spec(_final_spec(pages=40, spine="0.100"))

    assert geometry.spine_text_allowed is False


def test_ingram_adapter_refuses_unfinalized_cover_geometry():
    spec = PrintSpec()

    errors = IngramSparkAdapter.validate_spec(spec)

    assert any("page_count" in error for error in errors)
    assert any("spine_width_in" in error for error in errors)


def test_preflight_is_no_go_when_interior_bleed_is_not_supported():
    spec = PrintSpec(interior_bleed=True).finalize(page_count=200, spine_width_in="0.5")

    report = preflight_spec(spec)

    assert report.passed is False
    assert any(check.status == PreflightStatus.FAIL for check in report.checks)
    assert any("bleed" in check.message.lower() for check in report.checks)


def test_latex_renderer_emits_twosided_print_source_and_escapes_metadata():
    content = BookContent.from_chapters(
        title="Reason & Ethics",
        subtitle="A 100% Practical Edition",
        author="Rino_Books",
        chapters=(Chapter("Chapter #1", "First paragraph.\n\nSecond paragraph."),),
    )

    tex = LatexInteriorRenderer().render(content, PrintSpec())

    assert r"\documentclass[11pt,twoside,openright]{memoir}" in tex
    assert r"paperwidth=6in,paperheight=9in" in tex
    assert r"Reason \& Ethics" in tex
    assert r"A 100\% Practical Edition" in tex
    assert r"Rino\_Books" in tex
    assert r"\chapter{Chapter \#1}" in tex
    assert r"\widowpenalty=10000" in tex


def test_latex_renderer_refuses_bleed_interior_in_v1():
    content = BookContent.from_chapters(
        title="Book",
        author="Author",
        chapters=(Chapter("One", "Text"),),
    )

    with pytest.raises(ValueError, match="bleed"):
        LatexInteriorRenderer().render(content, PrintSpec(interior_bleed=True))


def test_manifest_binds_cover_to_geometry_fingerprint():
    spec = _final_spec()
    geometry = IngramSparkAdapter.cover_geometry(spec)
    manifest = PrintPackageManifest(book_id="book_7003", spec=spec, geometry=geometry)

    payload = manifest.as_dict()

    assert payload["book_id"] == "book_7003"
    assert payload["page_count"] == 286
    assert payload["artifacts"] == {"interior": "INTERIOR.pdf", "cover": "COVER.pdf"}
    assert payload["cover"]["geometry_fingerprint"] == geometry.fingerprint
