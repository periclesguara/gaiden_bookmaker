from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.test import SimpleTestCase

from gaiden.application.pipeline.source_extract import UnsupportedSourceFormatError, run_source_extract


EXPECTED_KEYS = {
    "schema",
    "status",
    "input_format",
    "original_file",
    "canonical_txt",
    "canonical_html",
    "images_dir",
    "meta_file",
    "warnings",
    "details",
}

EXPECTED_DETAIL_KEYS = {
    "title",
    "creators",
    "languages",
    "publisher",
    "rights",
    "spine_count",
    "toc_count",
    "images_count",
}


class SourceExtractTests(SimpleTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.created_codes: list[str] = []

    def tearDown(self):
        repo_root = Path(__file__).resolve().parents[2]
        for code in self.created_codes:
            shutil.rmtree(repo_root / "data" / "raw" / code, ignore_errors=True)
            shutil.rmtree(repo_root / "data" / "images" / code, ignore_errors=True)
        self.tempdir.cleanup()

    def test_txt_extract_generates_canonical_txt_and_html(self):
        source = Path(self.tempdir.name) / "input.txt"
        source.write_text("First paragraph.\n\nSecond paragraph.", encoding="utf-8")

        result = self.run_extract("book_9001", source)

        self.assert_contract(result, "txt")
        canonical_txt = self.repo_path(result["canonical_txt"])
        canonical_html = self.repo_path(result["canonical_html"])
        self.assertEqual(canonical_txt.read_text(encoding="utf-8"), "First paragraph.\n\nSecond paragraph.")
        self.assertIn("<pre>First paragraph.", canonical_html.read_text(encoding="utf-8"))

    def test_html_extract_generates_canonical_outputs_without_script_or_style(self):
        source = Path(self.tempdir.name) / "input.html"
        source.write_text(
            "<html><head><style>.x{}</style><script>alert(1)</script></head>"
            "<body><h1>Title</h1><p>Clean body.</p></body></html>",
            encoding="utf-8",
        )

        result = self.run_extract("book_9002", source)

        self.assert_contract(result, "html")
        canonical_txt = self.repo_path(result["canonical_txt"]).read_text(encoding="utf-8")
        self.assertIn("Title", canonical_txt)
        self.assertIn("Clean body.", canonical_txt)
        self.assertNotIn("alert", canonical_txt)
        self.assertNotIn(".x", canonical_txt)

    def test_epub_extract_generates_canonical_outputs_metadata_and_images(self):
        source = Path(self.tempdir.name) / "input.epub"
        self.write_epub(source)

        result = self.run_extract("book_9003", source)

        self.assert_contract(result, "epub")
        canonical_html = self.repo_path(result["canonical_html"]).read_text(encoding="utf-8")
        canonical_txt = self.repo_path(result["canonical_txt"]).read_text(encoding="utf-8")
        meta = json.loads(self.repo_path(result["meta_file"]).read_text(encoding="utf-8"))
        self.assertLess(canonical_html.index("Chapter One"), canonical_html.index("Chapter Two"))
        self.assertLess(canonical_txt.index("Chapter One"), canonical_txt.index("Chapter Two"))
        self.assertEqual(result["details"]["title"], "Fixture Book")
        self.assertEqual(result["details"]["creators"], ["Fixture Author"])
        self.assertEqual(result["details"]["languages"], ["en"])
        self.assertEqual(result["details"]["spine_count"], 2)
        self.assertEqual(result["details"]["toc_count"], 2)
        self.assertEqual(result["details"]["images_count"], 1)
        self.assertEqual(meta["schema"], "source_extract_v1")
        self.assertTrue((self.repo_path(result["images_dir"]) / "cover.png").exists())

    def test_invalid_format_fails_with_clear_error(self):
        source = Path(self.tempdir.name) / "input.pdf"
        source.write_bytes(b"%PDF-1.7")

        with self.assertRaisesRegex(UnsupportedSourceFormatError, "Unsupported source format: .pdf"):
            self.run_extract("book_9004", source)

    def test_contract_keys_are_uniform_for_all_formats(self):
        txt = Path(self.tempdir.name) / "contract.txt"
        html = Path(self.tempdir.name) / "contract.html"
        epub = Path(self.tempdir.name) / "contract.epub"
        txt.write_text("TXT", encoding="utf-8")
        html.write_text("<html><body>HTML</body></html>", encoding="utf-8")
        self.write_epub(epub)

        results = [
            self.run_extract("book_9010", txt),
            self.run_extract("book_9011", html),
            self.run_extract("book_9012", epub),
        ]

        for result in results:
            self.assertEqual(set(result.keys()), EXPECTED_KEYS)
            self.assertEqual(set(result["details"].keys()), EXPECTED_DETAIL_KEYS)
            self.assertEqual(result["schema"], "source_extract_v1")
            self.assertEqual(result["status"], "ok")

    def assert_contract(self, result: dict, input_format: str) -> None:
        self.assertEqual(set(result.keys()), EXPECTED_KEYS)
        self.assertEqual(set(result["details"].keys()), EXPECTED_DETAIL_KEYS)
        self.assertEqual(result["schema"], "source_extract_v1")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["input_format"], input_format)
        self.assertTrue(result["original_file"].startswith("data/raw/"))
        self.assertTrue(result["canonical_txt"].endswith("/source.txt"))
        self.assertTrue(result["canonical_html"].endswith("/source.html"))
        self.assertTrue(result["meta_file"].endswith("/source_meta.json"))

    def repo_path(self, path_value: str) -> Path:
        return Path(__file__).resolve().parents[2] / path_value

    def run_extract(self, book_code: str, source: Path) -> dict:
        self.created_codes.append(book_code)
        return run_source_extract(book_code, "en", source)

    def write_epub(self, path: Path) -> None:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip")
            zf.writestr(
                "META-INF/container.xml",
                """<?xml version="1.0"?>
                <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
                  <rootfiles>
                    <rootfile full-path="OPS/content.opf" media-type="application/oebps-package+xml"/>
                  </rootfiles>
                </container>""",
            )
            zf.writestr(
                "OPS/content.opf",
                """<?xml version="1.0"?>
                <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
                  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
                    <dc:title>Fixture Book</dc:title>
                    <dc:creator>Fixture Author</dc:creator>
                    <dc:language>en</dc:language>
                    <dc:publisher>Fixture Publisher</dc:publisher>
                    <dc:rights>Public domain</dc:rights>
                    <dc:date>2026</dc:date>
                    <dc:identifier>fixture-id</dc:identifier>
                  </metadata>
                  <manifest>
                    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
                    <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
                    <item id="c2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
                    <item id="cover" href="images/cover.png" media-type="image/png"/>
                  </manifest>
                  <spine>
                    <itemref idref="c1"/>
                    <itemref idref="c2"/>
                  </spine>
                </package>""",
            )
            zf.writestr(
                "OPS/nav.xhtml",
                """<html xmlns="http://www.w3.org/1999/xhtml"
                   xmlns:epub="http://www.idpf.org/2007/ops">
                   <body><nav epub:type="toc"><ol>
                   <li><a href="chapter1.xhtml">Chapter One</a></li>
                   <li><a href="chapter2.xhtml">Chapter Two</a></li>
                   </ol></nav></body></html>""",
            )
            zf.writestr("OPS/chapter1.xhtml", "<html><body><h1>Chapter One</h1><p>First body.</p></body></html>")
            zf.writestr("OPS/chapter2.xhtml", "<html><body><h1>Chapter Two</h1><p>Second body.</p></body></html>")
            zf.writestr("OPS/images/cover.png", b"\x89PNG\r\n\x1a\n")
