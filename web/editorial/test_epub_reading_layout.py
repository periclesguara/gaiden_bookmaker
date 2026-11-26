import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from bs4 import BeautifulSoup
from django.test import SimpleTestCase

from editorial.edition_renderer import EditionRenderer, RenderUnit, THEME_ROOT


class EpubReadingLayoutTests(SimpleTestCase):
    def test_master_theme_separates_illustration_from_complete_chapter_text(self):
        renderer = object.__new__(EditionRenderer)
        renderer.theme = json.loads((THEME_ROOT / "theme.json").read_text())
        renderer.edition = SimpleNamespace(language=SimpleNamespace(code="en"))
        renderer._metadata = {}
        self.assertIs(renderer.theme["illustration_opening_separate_xhtml"], True)
        unit = RenderUnit(
            "chapter", "chapter-001", "Arrival", "Chapter 1", "chapter_001.xhtml",
            '<figure><img src="../images/01.jpg" alt="Scene"/></figure>'
            '<p>First paragraph.</p><p>Last paragraph.</p>',
        )
        with TemporaryDirectory() as directory:
            renderer._render_units(Path(directory), [unit])
            opening = BeautifulSoup((Path(directory) / unit.opening_filename).read_text(), "xml")
            chapter = BeautifulSoup((Path(directory) / unit.filename).read_text(), "xml")
        self.assertEqual(unit.opening_filename, "chapter_001_opening.xhtml")
        self.assertEqual(opening.find("img")["src"], "../images/01.jpg")
        self.assertNotIn("First paragraph.", opening.get_text())
        self.assertIsNone(chapter.find("img"))
        self.assertEqual(
            [paragraph.get_text() for paragraph in chapter.select(".chapter-body p")],
            ["First paragraph.", "Last paragraph."],
        )
