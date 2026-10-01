from decimal import Decimal
from pathlib import Path
import shutil
import subprocess

import pytest
import cv2
import numpy as np

from gaiden.print_engine.cover_composition import cover_tex, design_cover_tex
from gaiden.print_engine.geometry import CoverGeometry
from gaiden.print_engine.specs import PrintSpec
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
        )
    )
    result = subprocess.run(["lualatex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "COVER.tex"], cwd=tmp_path, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout.decode(errors="replace")[-3000:]
    values = dict(line.split(":", 1) for line in subprocess.check_output(["pdfinfo", str(tmp_path / "COVER.pdf")], text=True).splitlines() if ":" in line)
    assert int(values["Pages"]) == 1
