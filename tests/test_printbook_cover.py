from decimal import Decimal
from pathlib import Path
import shutil
import subprocess

import pytest
import cv2
import numpy as np

from gaiden.print_engine.cover_composition import cover_tex, design_cover_tex, image_print_metrics
from gaiden.print_engine.geometry import CoverGeometry
from gaiden.print_engine.specs import PrintProvider, PrintSpec
from gaiden.print_engine.image_enhancement import enhance_print_image


def test_opencv_print_enhancement_preserves_real_pixel_dimensions(tmp_path):
    source = tmp_path / "source.jpg"
    target = tmp_path / "prepared.jpg"
    cv2.imwrite(str(source), np.full((120, 80, 3), 128, dtype=np.uint8))

    evidence = enhance_print_image(source, target, "print_safe")

    prepared = cv2.imread(str(target))
    assert prepared.shape[:2] == (120, 80)
    assert evidence["resized"] is False
    assert evidence["profile"] == "print_safe"
    assert evidence["engine"].startswith("opencv-")


def test_opencv_production_profile_reaches_requested_pixel_box(tmp_path):
    source = tmp_path / "source.jpg"
    target = tmp_path / "prepared.jpg"
    cv2.imwrite(str(source), np.full((300, 200, 3), 128, dtype=np.uint8))

    evidence = enhance_print_image(
        source, target, "production_300", minimum_width_px=600, minimum_height_px=900
    )

    assert evidence["width_px"] >= 600
    assert evidence["height_px"] >= 900
    assert evidence["resized"] is True
    assert evidence["scale_factor"] == 3.0


def test_cover_panels_use_final_geometry_and_fixed_filenames():
    geometry = CoverGeometry.from_spec(PrintSpec().finalize(page_count=100, spine_width_in=Decimal("0.25")))
    tex = cover_tex(geometry)
    assert f"paperwidth={geometry.spread_width_in}in" in tex
    assert f"paperheight={geometry.spread_height_in}in" in tex
    assert "FRONT.pdf" in tex and "BACK.pdf" in tex
    assert "keepaspectratio" in tex


def test_cover_builder_escapes_operator_text_and_binds_geometry():
    geometry = CoverGeometry.from_spec(PrintSpec().finalize(page_count=100, spine_width_in=Decimal("0.25")))
    tex = design_cover_tex(
        geometry,
        title="A & B",
        author="Author_1",
        subtitle="Edition #2",
        back_text="Line one\nLine two",
        spine_text="A & B",
        background_color="#112233",
        text_color="#FFFFFF",
        accent_color="#D4A24C",
        layout="modern",
    )
    assert f"paperwidth={geometry.spread_width_in}in" in tex
    assert r"A \& B" in tex
    assert r"Author\_1" in tex
    assert "112233" in tex and "D4A24C" in tex


def test_ready_art_layout_keeps_front_image_clean_and_reserves_barcode_area():
    geometry = CoverGeometry.from_spec(PrintSpec().finalize(page_count=190, spine_width_in=Decimal("0.4275")))

    tex = design_cover_tex(
        geometry,
        title="Already printed in artwork",
        author="Author",
        back_text="Back-cover copy.",
        back_heading="About this book",
        back_quote="A remarkable book",
        back_bio="Author biography",
        back_background_color="#F4EBDD",
        back_text_color="#2D2926",
        back_accent_color="#8A5A32",
        spine_text="Book · Author",
        layout="art_only",
        image_name="COVER_IMAGE.jpg",
        image_width_px=1800,
        image_height_px=2700,
        reserve_barcode=True,
    )

    assert "COVER_IMAGE.jpg" in tex
    assert "Already printed in artwork" not in tex
    assert "Back-cover copy." in tex
    assert "About this book" in tex
    assert "Author biography" in tex
    assert "F4EBDD" in tex and "8A5A32" in tex
    assert "1.75in" in tex


def test_effective_dpi_uses_printed_box_after_fit_and_scale():
    metrics = image_print_metrics(
        1800, 2700, Decimal("6.125"), Decimal("9.25"), fit="cover", scale_percent=150,
    )
    assert metrics["printed_height_in"] == Decimal("13.875")
    assert int(metrics["effective_dpi_y"]) == 194
    assert metrics["effective_dpi_x"] == metrics["effective_dpi_y"]


def test_kdp_requires_at_least_eighty_pages_for_spine_text():
    short = CoverGeometry.from_spec(PrintSpec(provider=PrintProvider.AMAZON_KDP).finalize(page_count=79, spine_width_in="0.20"))
    eligible = CoverGeometry.from_spec(PrintSpec(provider=PrintProvider.AMAZON_KDP).finalize(page_count=80, spine_width_in="0.20"))
    assert short.spine_text_allowed is False
    assert eligible.spine_text_allowed is True


def test_back_image_and_spine_typography_are_vector_layout_inputs():
    geometry = CoverGeometry.from_spec(PrintSpec().finalize(page_count=100, spine_width_in="0.4"))
    tex = design_cover_tex(
        geometry, title="Book", author="Author", layout="classic",
        image_name="FRONT.jpg", image_width_px=1800, image_height_px=2700,
        back_image_name="BACK.jpg", back_image_width_px=1800, back_image_height_px=2700,
        spine_title="Book", spine_author="Author", spine_font_size_pt=8,
        image_offset_x_mm="1.2", back_image_rotation_deg="2",
    )
    assert "FRONT.jpg" in tex and "BACK.jpg" in tex
    assert r"\newfontfamily\spinefont" in tex
    assert "Book · Author" in tex
    assert "rotate=2" in tex


def test_front_text_adjustments_and_back_footer_are_exported():
    geometry = CoverGeometry.from_spec(PrintSpec().finalize(page_count=100, spine_width_in="0.4"))
    tex = design_cover_tex(
        geometry,
        title="Manual de Epicteto",
        author="Epicteto de Hierápolis",
        layout="classic",
        front_title_y_percent=30,
        front_author_y_percent=78,
        front_text_scale_percent=120,
        front_text_align="left",
        cover_font_family="TeX Gyre Pagella",
        back_footer="Coleção Mestres Estoicos · RinoBooks",
    )

    assert r"\raggedright\titlefont\color{titlecolor}" in tex
    assert "Epicteto de Hierápolis" in tex
    assert "Coleção Mestres Estoicos · RinoBooks" in tex
    assert r"\setmainfont{TeX Gyre Pagella}" in tex
    assert r"\begin{adjustbox}{max width=" in tex


def test_editorial_cover_fields_typography_and_spine_background_reach_pdf_source():
    geometry = CoverGeometry.from_spec(PrintSpec().finalize(page_count=160, spine_width_in="0.5"))
    tex = design_cover_tex(
        geometry,
        title="Manual de Epicteto",
        subtitle="Encheirídion",
        author="Epicteto de Hierápolis",
        language="Português brasileiro",
        publisher="RinoBooks",
        layout="classic",
        cover_font_family="EB Garamond",
        front_title_font_family="GFS Didot",
        front_subtitle_font_family="Berenis ADF Pro",
        front_author_font_family="Gentium Book Plus",
        front_language_font_family="Charis SIL",
        front_publisher_font_family="Fira Sans",
        front_title_color="#F7F1E3",
        front_subtitle_color="#C9A45C",
        front_author_color="#D8D0C0",
        front_language_color="#AFC7D8",
        front_publisher_color="#B99A58",
        front_title_font_size_pt="34",
        front_subtitle_font_size_pt="16",
        front_author_font_size_pt="13",
        front_language_font_size_pt="8",
        front_publisher_font_size_pt="9",
        front_title_weight="regular",
        front_title_style="small_caps",
        front_title_letter_spacing_pt="1.2",
        front_subtitle_weight="bold",
        front_subtitle_style="italic",
        front_subtitle_letter_spacing_pt="0.5",
        front_author_style="italic",
        front_language_style="small_caps",
        front_language_letter_spacing_pt="0.8",
        front_publisher_weight="bold",
        front_title_x_percent=44,
        front_subtitle_x_percent=56,
        front_subtitle_y_percent=38,
        front_author_x_percent=48,
        front_language_x_percent=50,
        front_language_y_percent=84,
        front_publisher_x_percent=52,
        back_heading="Sobre o livro",
        back_text="Texto editorial da contracapa.",
        back_heading_font_size_pt="18",
        back_body_font_size_pt="11",
        back_text_align="justify",
        back_text_x_percent=54,
        back_text_y_percent=18,
        spine_image_name="SPINE_IMAGE.jpg",
        spine_image_width_px=600,
        spine_image_height_px=2700,
        spine_text_x_percent=55,
        spine_text_y_percent=47,
    )

    assert r"\setmainfont{EB Garamond}" in tex
    assert r"\newfontfamily\titlefont{GFS Didot}" in tex
    assert r"\newfontfamily\subtitlefont{Berenis ADF Pro}" in tex
    assert r"\newfontfamily\authorfont{Gentium Book Plus}" in tex
    assert r"\newfontfamily\languagefont{Charis SIL}" in tex
    assert r"\newfontfamily\publisherfont{Fira Sans}" in tex
    assert "F7F1E3" in tex and "C9A45C" in tex and "AFC7D8" in tex and "B99A58" in tex
    assert r"\scshape\addfontfeatures{LetterSpace=1.2}" in tex
    assert r"\bfseries\itshape\addfontfeatures{LetterSpace=0.5}" in tex
    assert "RinoBooks" in tex
    assert "Português brasileiro" in tex
    assert "Português brasileiro · RinoBooks" not in tex
    assert r"\fontsize{34}" in tex
    assert r"\fontsize{18}" in tex and r"\fontsize{11}" in tex
    assert r"\justifying" in tex
    assert "SPINE_IMAGE.jpg" in tex


@pytest.mark.skipif(not shutil.which("lualatex") or not shutil.which("pdfinfo"), reason="LuaLaTeX and Poppler required")
def test_composed_cover_is_one_page_with_full_wrap_dimensions(tmp_path):
    geometry = CoverGeometry.from_spec(PrintSpec().finalize(page_count=100, spine_width_in=Decimal("0.25")))
    panel = r"\documentclass{article}\usepackage[paperwidth=6.125in,paperheight=9.25in,margin=0in]{geometry}\pagestyle{empty}\begin{document}\null\end{document}"
    for name, content in (("FRONT", panel), ("BACK", panel), ("COVER", cover_tex(geometry))):
        (tmp_path / f"{name}.tex").write_text(content)
        result = subprocess.run(["lualatex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", f"{name}.tex"], cwd=tmp_path, capture_output=True, timeout=60)
        assert result.returncode == 0, result.stdout.decode(errors="replace")[-2000:]
    result = subprocess.run(["pdfinfo", str(tmp_path / "COVER.pdf")], capture_output=True, text=True, timeout=10, check=True)
    values = dict(line.split(":", 1) for line in result.stdout.splitlines() if ":" in line)
    assert int(values["Pages"]) == 1
    dimensions = values["Page size"].split()
    assert float(dimensions[0]) == pytest.approx(float(geometry.spread_width_in) * 72, abs=0.1)
    assert float(dimensions[2]) == pytest.approx(float(geometry.spread_height_in) * 72, abs=0.1)


@pytest.mark.skipif(not shutil.which("lualatex") or not shutil.which("pdfinfo"), reason="LuaLaTeX and Poppler required")
def test_cover_builder_compiles_one_page(tmp_path):
    geometry = CoverGeometry.from_spec(PrintSpec().finalize(page_count=100, spine_width_in=Decimal("0.25")))
    (tmp_path / "COVER.tex").write_text(
        design_cover_tex(
            geometry,
            title="The Devil in Paris",
            author="RinoBooks",
            back_text="A short description for the back cover.",
            spine_text="The Devil in Paris",
            layout="classic",
            cover_font_family="EB Garamond",
            spine_font_family="EB Garamond",
            front_title_font_family="GFS Didot",
            front_subtitle_font_family="Berenis ADF Pro",
            front_author_font_family="Gentium Book Plus",
            front_publisher_font_family="Fira Sans",
        )
    )
    result = subprocess.run(["lualatex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "COVER.tex"], cwd=tmp_path, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout.decode(errors="replace")[-3000:]
    values = dict(line.split(":", 1) for line in subprocess.check_output(["pdfinfo", str(tmp_path / "COVER.pdf")], text=True).splitlines() if ":" in line)
    assert int(values["Pages"]) == 1
