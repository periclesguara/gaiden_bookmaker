from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup
from django.test import TestCase
from django.urls import reverse

from editorial.edition_renderer import (
    EditionRenderer,
    THEME_NAME,
    invalidate_premium_render,
    package_hashes,
    preview_hashes,
)
from editorial.models import Contributor, Edition, EditionText, Language, Seal, Work
from gaiden.infrastructure import storage
from pipeline.models import BookEditionTemplate


class PremiumEpubRendererTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        language = Language.objects.create(code="en", name="English", native_name="English")
        seal = Seal.objects.create(slug="mantaquest", name="MantaQuest")
        author = Contributor.objects.create(name="Example Author")
        work = Work.objects.create(
            code="book_0901",
            title="A Premium Test Book",
            original_language=language,
            author=author,
            publisher="RinoBooks",
            year=1901,
            is_public_domain=True,
        )
        cls.edition = Edition.objects.create(
            work=work,
            language=language,
            seal=seal,
            title="A Premium Test Book",
            subtitle="A Canonical Edition",
            author="Example Author",
            publisher="RinoBooks",
            edition_year=2026,
            publication_year=2026,
            imprint_name="MantaQuest",
            seal_name="MantaQuest",
        )
        EditionText.objects.create(
            edition=cls.edition,
            normalized_text=(
                "# Part One\n\n"
                "## Chapter 1 — Arrival\n\n"
                "![A true illustration](assets/images/scene.png)\n\n"
                "The first paragraph opens the story.\n\n"
                "The following paragraph is indented.\n\n"
                "## Chapter 2 — Choice\n\n"
                "Another first paragraph.\n\n"
                "Another following paragraph.\n\n"
                "# Appendix\n\n"
                "Editorial notes belong in backmatter.\n\n"
                "# The End\n"
            ),
        )
        BookEditionTemplate.objects.create(
            book_code=work.code,
            language="en",
            title=cls.edition.title,
            subtitle=cls.edition.subtitle,
            author_name=cls.edition.author,
            publication_year=2026,
            frontispiece_text="A Premium Test Book\nExample Author\nMantaQuest",
            copyright_text="Original work in the public domain.\n\nThis edition © 2026 RinoBooks.",
            about_edition_text="This canonical test edition validates the premium digital workflow.",
            imprint_name="MantaQuest",
            edition_year=2026,
            edition_copyright_holder="RinoBooks",
        )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.old_storage_root = os.environ.get("GAIDEN_STORAGE_ROOT")
        os.environ["GAIDEN_STORAGE_ROOT"] = str(Path(cls.temp_dir.name) / "data")
        cls.edition = Edition.objects.get(pk=cls.edition.pk)

        cover = storage.covers_dir(cls.edition.work.code, cls.edition.language.code) / "cover.jpg"
        cover.parent.mkdir(parents=True, exist_ok=True)
        cover.write_bytes(
            base64.b64decode(
                "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABD/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/EB//xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/EB//xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/EB//2Q=="
            )
        )
        image = storage.builds_dir(cls.edition.work.code, cls.edition.language.code) / "assets" / "images" / "scene.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(
            base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        )
        cls.renderer = EditionRenderer(cls.edition)
        cls.result = cls.renderer.render()

    @classmethod
    def tearDownClass(cls):
        if cls.old_storage_root is None:
            os.environ.pop("GAIDEN_STORAGE_ROOT", None)
        else:
            os.environ["GAIDEN_STORAGE_ROOT"] = cls.old_storage_root
        cls.temp_dir.cleanup()
        super().tearDownClass()

    def _opf(self):
        return BeautifulSoup((self.result.root / "EPUB" / "content.opf").read_text(encoding="utf-8"), "xml")

    def _text_documents(self):
        return sorted((self.result.root / "EPUB" / "text").glob("*.xhtml"))

    def _assert_links(self, document: Path, base: Path, attribute: str = "href"):
        soup = BeautifulSoup(document.read_text(encoding="utf-8"), "xml")
        for tag in soup.find_all(attrs={attribute: True}):
            href = tag.get(attribute)
            if not href or href.startswith(("http:", "https:", "mailto:")):
                continue
            relative, _, fragment = href.partition("#")
            target = (base / relative).resolve() if relative else document.resolve()
            self.assertTrue(target.exists(), href)
            if fragment and target.suffix == ".xhtml":
                target_soup = BeautifulSoup(target.read_text(encoding="utf-8"), "xml")
                self.assertIsNotNone(target_soup.find(id=fragment), href)

    def test_default_premium_theme_is_used(self):
        self.assertEqual(self.renderer.theme["name"], THEME_NAME)
        self.assertTrue((self.result.root / "EPUB/styles/gaiden-premium.css").exists())

    def test_asset_change_invalidates_approval(self):
        self.renderer.approve_preview()
        invalidate_premium_render(self.edition, "cover_changed")
        state = self.renderer._load_state()
        self.assertEqual(state["status"], "EDITION_RENDER_REQUIRED")
        self.assertEqual(state["approved_fingerprint"], "")

    def test_preview_and_epub_use_same_css_hash(self):
        result = self.renderer.approve_preview()
        epub = self.renderer.build_epub("test.epub")
        self.assertEqual(
            preview_hashes(result)["EPUB/styles/gaiden-premium.css"],
            package_hashes(epub)["EPUB/styles/gaiden-premium.css"],
        )

    def test_preview_and_epub_use_same_image_hashes(self):
        result = self.renderer.approve_preview()
        epub = self.renderer.build_epub("test.epub")
        preview = preview_hashes(result)
        package = package_hashes(epub)
        for path, digest in preview.items():
            if path.startswith("EPUB/images/"):
                self.assertEqual(package[path], digest)

    def test_each_chapter_has_unique_id(self):
        ids = []
        for path in (self.result.root / "EPUB/text").glob("chapter_*.xhtml"):
            soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
            ids.append(soup.find("section").get("id"))
        self.assertEqual(len(ids), len(set(ids)))

    def test_each_chapter_has_single_h1(self):
        counts = {}
        for path in (self.result.root / "EPUB/text").glob("chapter_*.xhtml"):
            soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
            key = re.match(r"chapter_\d+", path.name).group(0)
            counts[key] = counts.get(key, 0) + len(soup.find_all("h1"))
        self.assertTrue(counts)
        self.assertTrue(all(count == 1 for count in counts.values()), counts)

    def test_visible_contents_links_are_valid(self):
        path = self.result.root / "EPUB/text/contents.xhtml"
        self._assert_links(path, path.parent)

    def test_nav_links_are_valid(self):
        path = self.result.root / "EPUB/nav.xhtml"
        self._assert_links(path, path.parent)

    def test_ncx_links_are_valid(self):
        path = self.result.root / "EPUB/toc.ncx"
        self._assert_links(path, path.parent, attribute="src")

    def test_cover_is_first_spine_item(self):
        opf = self._opf()
        hrefs = {item.get("id"): item.get("href") for item in opf.find_all("item")}
        spine = [hrefs[item.get("idref")] for item in opf.find_all("itemref")]
        self.assertEqual(spine[0], "text/cover.xhtml")

    def test_cover_image_property_exists(self):
        self.assertIsNotNone(self._opf().find("item", attrs={"properties": re.compile("cover-image")}))

    def test_no_fixed_body_dimensions(self):
        css = (self.result.root / "EPUB/styles/gaiden-premium.css").read_text(encoding="utf-8")
        self.assertNotRegex(css, r"(?:width|height)\s*:\s*\d+px")
        self.assertNotRegex(css, r"position\s*:\s*(?:absolute|fixed)")

    def test_body_is_not_globally_centered(self):
        css = (self.result.root / "EPUB/styles/gaiden-premium.css").read_text(encoding="utf-8")
        body = re.search(r"body\s*\{([^}]*)\}", css, re.DOTALL).group(1)
        self.assertNotIn("text-align: center", body)

    def test_first_paragraph_has_no_indent_class(self):
        soup = BeautifulSoup((self.result.root / "EPUB/text/chapter_001.xhtml").read_text(encoding="utf-8"), "xml")
        self.assertIn("first-paragraph", soup.select_one(".chapter-body p").get("class"))

    def test_following_paragraphs_have_indent(self):
        css = (self.result.root / "EPUB/styles/gaiden-premium.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.chapter-body p\s*\{[^}]*text-indent:\s*1\.25em")

    def test_images_are_responsive(self):
        css = (self.result.root / "EPUB/styles/gaiden-premium.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"img\s*\{[^}]*max-width:\s*100%")

    def test_images_preserve_aspect_ratio(self):
        css = (self.result.root / "EPUB/styles/gaiden-premium.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"img\s*\{[^}]*height:\s*auto")

    def test_no_duplicate_headings(self):
        headings = []
        for path in (self.result.root / "EPUB/text").glob("chapter_*.xhtml"):
            soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
            headings.extend(h1.get_text(" ", strip=True) for h1 in soup.find_all("h1"))
        self.assertEqual(len(headings), len(set(headings)))

    def test_illustrated_chapter_uses_separate_opening_xhtml(self):
        opening = self.result.root / "EPUB/text/chapter_001_opening.xhtml"
        body = self.result.root / "EPUB/text/chapter_001.xhtml"
        self.assertTrue(opening.exists())
        self.assertEqual(len(BeautifulSoup(opening.read_text(), "xml").find_all("h1")), 1)
        self.assertEqual(len(BeautifulSoup(body.read_text(), "xml").find_all("h1")), 0)
        self.assertLess(self.result.spine.index("text/chapter_001_opening.xhtml"), self.result.spine.index("text/chapter_001.xhtml"))

    def test_frontmatter_does_not_repeat_its_heading(self):
        for filename in ("copyright.xhtml", "about_this_edition.xhtml"):
            soup = BeautifulSoup((self.result.root / "EPUB/text" / filename).read_text(encoding="utf-8"), "xml")
            heading = soup.h1.get_text(" ", strip=True).casefold()
            paragraphs = [p.get_text(" ", strip=True).casefold() for p in soup.find_all("p")]
            self.assertNotIn(heading, paragraphs)

    def test_no_duplicate_images(self):
        refs = []
        for path in self._text_documents():
            soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
            refs.extend(image.get("src") for image in soup.find_all("img") if "cover" not in (image.get("class") or []))
        self.assertEqual(len(refs), len(set(refs)))

    def test_no_internal_pipeline_markers_leak(self):
        text = "\n".join(path.read_text(encoding="utf-8") for path in self._text_documents())
        self.assertNotRegex(text, r"::: ?pagebreak|RELEASE\s+STAMP|CH\d+:\d+")

    def test_no_gutenberg_material_leaks(self):
        text = "\n".join(path.read_text(encoding="utf-8") for path in self._text_documents())
        self.assertNotIn("Project Gutenberg", text)

    def test_no_empty_isbn(self):
        opf = (self.result.root / "EPUB/content.opf").read_text(encoding="utf-8")
        self.assertNotRegex(opf, r"(?i)isbn\s*[:=]\s*(?:<|$)")

    def test_language_metadata_matches_edition(self):
        opf = self._opf()
        language = opf.find("language")
        self.assertIsNotNone(language)
        self.assertEqual(language.get_text(strip=True), self.edition.language.code)

    def test_the_end_page_exists_when_enabled(self):
        self.assertTrue((self.result.root / "EPUB/text/the_end.xhtml").exists())
        self.assertIn("text/the_end.xhtml", self.result.spine)

    def test_epubcheck_passes(self):
        if not shutil.which("epubcheck"):
            self.skipTest("epubcheck is not installed")
        self.renderer.approve_preview()
        epub = self.renderer.build_epub("epubcheck.epub")
        result = subprocess.run(["epubcheck", str(epub)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_renderer_is_idempotent(self):
        first = self.renderer.render()
        second = self.renderer.render()
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.spine, second.spine)

    def test_preview_route_serves_canonical_artifacts(self):
        response = self.client.get(reverse("premium_epub_preview", kwargs={"edition_id": self.edition.id}))
        self.assertEqual(response.status_code, 200)
        artifact = self.client.get(
            reverse(
                "premium_epub_asset",
                kwargs={"edition_id": self.edition.id, "relative_path": "EPUB/text/chapter_001.xhtml"},
            )
        )
        self.assertEqual(artifact.status_code, 200)
        self.assertEqual(artifact.headers.get("X-Frame-Options"), "SAMEORIGIN")
