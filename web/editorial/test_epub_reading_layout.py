import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from bs4 import BeautifulSoup
from django.test import SimpleTestCase

from editorial.edition_renderer import EditionRenderer, RenderUnit, THEME_ROOT


class EpubReadingLayoutTests(SimpleTestCase):
    def test_master_theme_keeps_illustration_and_complete_text_without_extra_document(self):
        renderer = object.__new__(EditionRenderer)
        renderer.theme = json.loads((THEME_ROOT / "theme.json").read_text())
        renderer.edition = SimpleNamespace(language=SimpleNamespace(code="en"))
        renderer._metadata = {}
        self.assertIs(renderer.theme["illustration_opening_separate_xhtml"], False)
        unit = RenderUnit(
            "chapter", "chapter-001", "Arrival", "Chapter 1", "chapter_001.xhtml",
            '<figure><img src="../images/01.jpg" alt="Scene"/></figure>'
            '<p>First paragraph.</p><p>Last paragraph.</p>',
        )
        with TemporaryDirectory() as directory:
            renderer._render_units(Path(directory), [unit])
            chapter = BeautifulSoup((Path(directory) / unit.filename).read_text(), "xml")
            self.assertEqual([path.name for path in Path(directory).glob("*.xhtml")], [unit.filename])
        self.assertEqual(unit.opening_filename, "")
        self.assertEqual(chapter.find("img")["src"], "../images/01.jpg")
        self.assertEqual(chapter.select_one(".chapter-body figure").find_next_sibling("p").get_text(), "First paragraph.")
        self.assertEqual(
            [paragraph.get_text() for paragraph in chapter.select(".chapter-body p")],
            ["First paragraph.", "Last paragraph."],
        )
