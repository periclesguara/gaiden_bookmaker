from __future__ import annotations

from decimal import Decimal
from subprocess import CompletedProcess

import pytest

from gaiden.print_engine import preflight as preflight_module
from gaiden.print_engine.cover import CoverLayout
from gaiden.print_engine.geometry import CoverGeometry
from gaiden.print_engine.latex import BookContent, Chapter, LatexInteriorRenderer
from gaiden.print_engine.package import PrintPackageManifest
from gaiden.print_engine.preflight import PreflightStatus, preflight_pdfs, preflight_spec
from gaiden.print_engine.providers.ingramspark import IngramSparkAdapter
from gaiden.print_engine.specs import PrintSpec


def _final_spec(*, pages: int = 286, spine: str = "0.600") -> PrintSpec:
    return PrintSpec().finalize(page_count=pages, spine_width_in=spine)


def test_cover_geometry_matches_finalized_6x9_interior():
    geometry = CoverGeometry.from_spec(_final_spec())

    assert geometry.spread_width_in == Decimal("12.850")
    assert geometry.spread_height_in == Decimal("9.250")
    assert geometry.page_count == 286
    assert geometry.spine_text_allowed is True


def test_cover_layout_places_back_spine_and_front_on_same_canvas():
    spec = _final_spec()
    layout = CoverLayout.from_spec(spec)

    assert layout.back.x == Decimal("0.125")
    assert layout.spine.x == Decimal("6.125")
    assert layout.front.x == Decimal("6.725")
    assert layout.front.width == Decimal("6")
    assert layout.canvas.width == Decimal("12.850")
    assert layout.front_safe.x == Decimal("6.975")


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


def test_ingram_adapter_rejects_text_margin_inside_safe_area():
    spec = PrintSpec(outer_margin_in=Decimal("0.4")).finalize(
        page_count=200,
        spine_width_in="0.5",
    )

    errors = IngramSparkAdapter.validate_spec(spec)

    assert any("outer_margin_in" in error for error in errors)


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
    assert r"inner=0.75in,outer=0.5in,top=0.55in,bottom=0.65in" in tex
    assert r"Reason \& Ethics" in tex
    assert r"A 100\% Practical Edition" in tex
    assert r"Rino\_Books" in tex
    assert r"\chapter{Chapter \#1}" in tex
    assert r"\widowpenalty=10000" in tex
    assert r"\ifodd\value{page}\else" in tex


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


def test_manifest_rejects_geometry_from_another_finalized_spec():
    spec = _final_spec(pages=286, spine="0.600")
    stale_geometry = CoverGeometry.from_spec(_final_spec(pages=302, spine="0.640"))

    with pytest.raises(ValueError, match="does not match"):
        PrintPackageManifest(book_id="book_7003", spec=spec, geometry=stale_geometry)


def test_manifest_rejects_artifact_path_traversal():
    spec = _final_spec()

    with pytest.raises(ValueError, match="safe PDF filename"):
        PrintPackageManifest(
            book_id="book_7003",
            spec=spec,
            geometry=CoverGeometry.from_spec(spec),
            cover_pdf="../COVER.pdf",
        )


def test_print_spec_rejects_non_finite_dimensions_and_fractional_pages():
    with pytest.raises(ValueError, match="finite"):
        PrintSpec(trim_width_in="NaN")
    with pytest.raises(ValueError, match="positive integer"):
        PrintSpec().finalize(page_count=12.5, spine_width_in="0.1")


def test_preflight_fails_when_installed_pdf_tools_cannot_read_files(tmp_path, monkeypatch):
    interior = tmp_path / "INTERIOR.pdf"
    cover = tmp_path / "COVER.pdf"
    interior.write_bytes(b"not a PDF")
    cover.write_bytes(b"not a PDF")
    monkeypatch.setattr(preflight_module.shutil, "which", lambda _tool: "/usr/bin/tool")
    monkeypatch.setattr(
        preflight_module,
        "_run_tool",
        lambda command: CompletedProcess(command, 1, stdout="", stderr="invalid PDF"),
    )

    report = preflight_pdfs(_final_spec(), interior_pdf=interior, cover_pdf=cover)

    assert report.passed is False
    assert report.fully_verified is False
    assert any(
        check.code == "interior.pdfinfo" and check.status == PreflightStatus.FAIL
        for check in report.checks
    )


def test_preflight_marks_unavailable_pdf_tools_as_skipped(tmp_path, monkeypatch):
    interior = tmp_path / "INTERIOR.pdf"
    cover = tmp_path / "COVER.pdf"
    interior.touch()
    cover.touch()
    monkeypatch.setattr(preflight_module.shutil, "which", lambda _tool: None)

    report = preflight_pdfs(_final_spec(), interior_pdf=interior, cover_pdf=cover)

    assert report.passed is True
    assert report.fully_verified is False
    assert report.requires_review is True
    assert any(check.status == PreflightStatus.SKIP for check in report.checks)


def test_ingram_adapter_rejects_odd_final_page_count():
    spec = _final_spec(pages=285, spine="0.600")

    errors = IngramSparkAdapter.validate_spec(spec)

    assert any("must be even" in error for error in errors)
