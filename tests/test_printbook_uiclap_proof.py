from decimal import Decimal
from pathlib import Path

import cv2
import numpy as np
import pytest

from gaiden.print_engine.cover_composition import design_cover_tex
from gaiden.print_engine.geometry import CoverGeometry
from gaiden.print_engine.image_enhancement import enhance_print_image
from gaiden.print_engine.specs import Binding, PrintProvider, PrintSpec


@pytest.mark.parametrize('pages', [29, 30, 59])
def test_uiclap_short_book_has_zero_spine_and_no_printed_spine_content(pages):
    spec = PrintSpec(provider=PrintProvider.UICLAP, binding=Binding.PAPERBACK).finalize(page_count=pages, spine_width_in='0')
    geometry = CoverGeometry.from_spec(spec)
    assert geometry.spine_width_in == 0
    assert not geometry.spine_text_allowed
    assert geometry.spread_width_in == geometry.trim_width_in * 2 + geometry.bleed_in * 2
    tex = design_cover_tex(geometry, title='Front', author='Author', spine_title='Spine copy', spine_image_name='SPINE.jpg', spine_image_width_px=100, spine_image_height_px=100)
    assert 'SPINE.jpg' not in tex
    assert 'Spine copy' not in tex


def test_uiclap_sixty_pages_and_other_providers_require_positive_spine():
    with pytest.raises(ValueError, match='spine width'):
        PrintSpec(provider=PrintProvider.UICLAP).finalize(page_count=60, spine_width_in='0')
    with pytest.raises(ValueError, match='spine width'):
        PrintSpec(provider=PrintProvider.AMAZON_KDP).finalize(page_count=30, spine_width_in='0')
    with pytest.raises(ValueError, match='zero spine width'):
        PrintSpec(provider=PrintProvider.UICLAP).finalize(page_count=59, spine_width_in='0.1')
    bound = CoverGeometry.from_spec(PrintSpec(provider=PrintProvider.UICLAP).finalize(page_count=60, spine_width_in='0.1'))
    assert bound.spine_text_allowed is True


def test_uiclap_image_enhancement_preserves_original_and_labels_interpolation(tmp_path):
    original = tmp_path / 'original.png'
    target = tmp_path / 'derived.png'
    cv2.imwrite(str(original), np.full((150, 100, 3), 128, dtype=np.uint8))
    before = original.read_bytes()
    evidence = enhance_print_image(original, target, 'uiclap_300', minimum_width_px=200, minimum_height_px=300)
    assert original.read_bytes() == before
    assert (evidence['source_width_px'], evidence['source_height_px']) == (100, 150)
    assert (evidence['width_px'], evidence['height_px']) == (200, 300)
    assert evidence['resized'] is True
    assert evidence['resolution_note'] == 'interpolated'


def test_uiclap_enhancement_rejects_unbounded_or_in_place_output(tmp_path):
    original = tmp_path / 'original.png'
    cv2.imwrite(str(original), np.zeros((20, 20, 3), dtype=np.uint8))
    before = original.read_bytes()
    with pytest.raises(ValueError):
        enhance_print_image(original, original, 'uiclap_300', minimum_width_px=30, minimum_height_px=30)
    with pytest.raises(ValueError):
        enhance_print_image(original, tmp_path / 'huge.png', 'uiclap_300', minimum_width_px=100000, minimum_height_px=100000)
    assert original.read_bytes() == before
    assert not (tmp_path / 'huge.png').exists()


@pytest.mark.skipif(not __import__('shutil').which('lualatex'), reason='LuaLaTeX required')
def test_stapled_uiclap_proof_compiles_without_a_spine(tmp_path):
    import subprocess
    geometry = CoverGeometry.from_spec(PrintSpec(provider=PrintProvider.UICLAP, cover_bleed_in=Decimal('5') / Decimal('25.4')).finalize(page_count=30, spine_width_in='0'))
    tex = design_cover_tex(geometry, title='Front', author='Author', spine_title='Excluded')
    (tmp_path / 'cover.tex').write_text(tex, encoding='utf-8')
    subprocess.run(['lualatex', '-interaction=nonstopmode', '-halt-on-error', 'cover.tex'], cwd=tmp_path, check=True, capture_output=True, timeout=60)
    assert (tmp_path / 'cover.pdf').is_file()
    assert 'Excluded' not in tex


def test_oversized_source_is_rejected_before_enhancement(tmp_path, monkeypatch):
    source = tmp_path / 'input.png'
    source.write_bytes(b'placeholder')
    class OversizedImage:
        ndim = 3
        shape = (8000, 8000, 3)
    monkeypatch.setattr(cv2, 'imdecode', lambda *_: OversizedImage())
    with pytest.raises(ValueError, match='original excede'):
        enhance_print_image(source, tmp_path / 'output.png', 'uiclap_300', minimum_width_px=10, minimum_height_px=10)
    assert not (tmp_path / 'output.png').exists()
