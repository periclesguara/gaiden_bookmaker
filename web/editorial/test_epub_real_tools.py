import hashlib
import os
import shutil
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
from xml.etree import ElementTree

from django.test import SimpleTestCase

from editorial import kdp_mode


@skipUnless(os.environ.get("GAIDEN_RUN_EPUB_TOOLS") == "1", "Explicit real-tool integration run required")
class EpubRealToolsTests(SimpleTestCase):
    def test_representative_books_are_validated_with_real_epubcheck(self):
        self.assertIsNotNone(shutil.which("pandoc"))
        self.assertIsNotNone(shutil.which("epubcheck"))
        manuscripts = {
            "en": '# Chapter One\n\nThe door was open.\n\n> Dear friend,\n>\n> Keep the letter.\n\n# Chapter Two\n\nShe returned at dawn.\n',
            "pt-br": '# Diário\n\n*Quarta-feira*\n\nUma linha  \nOutra linha\n\n# Notas\n\nUma observação.[^1]\n\n[^1]: Uma nota preservada.\n',
            "fr": '# Méthode\n\n| Terme | Valeur |\n| --- | --- |\n| Exemple | 2 |\n\n# Résultats\n\nUne conclusion précise.\n',
        }
        with TemporaryDirectory() as directory:
            for language, manuscript in manuscripts.items():
                with self.subTest(language=language):
                    root = Path(directory) / language
                    root.mkdir()
                    source = root / "kdp_merged.md"
                    source.write_text(manuscript)
                    digest = hashlib.sha256(source.read_bytes()).hexdigest()
                    edition = SimpleNamespace(
                        title="Fixture title", subtitle="Approved subtitle", author="Fixture Author",
                        language=SimpleNamespace(code=language), language_code=language,
                        work=SimpleNamespace(code="fixture", title="Fixture title", author=None),
                        publisher="Fixture Press", imprint_name="", seal_name="",
                        adapter="", translator="", editor="", publication_year=2026,
                        city="", country="", cover_filepath="",
                    )
                    with patch.object(kdp_mode, "builds_dir", return_value=root), patch.object(kdp_mode, "_resolve_cover_path", return_value=None):
                        epub = kdp_mode.build_epub_for_edition(edition)
                        kdp_mode.run_epubcheck_for_edition(edition, artifact_path=epub)
                    self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
                    with zipfile.ZipFile(epub) as archive:
                        self.assertEqual(archive.namelist()[0], "mimetype")
                        self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
                        self.assertEqual(archive.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
                        package = next(name for name in archive.namelist() if name.endswith(".opf"))
                        metadata = ElementTree.fromstring(archive.read(package))
                        namespace = "{http://purl.org/dc/elements/1.1/}"
                        self.assertEqual(metadata.find(f".//{namespace}title").text, edition.title)
                        self.assertEqual(metadata.find(f".//{namespace}creator").text, edition.author)
                        for name in archive.namelist():
                            if name.endswith(".xhtml"):
                                ElementTree.fromstring(archive.read(name))
