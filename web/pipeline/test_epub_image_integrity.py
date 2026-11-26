import json
import io
import shutil
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bs4 import BeautifulSoup
from django.test import SimpleTestCase

from editorial.edition_renderer import EditionRenderer, RenderUnit, THEME_ROOT
from pipeline.services import md_transform
from pipeline.views import (
    _clear_consolidated_images_for_edition,
    _consolidate_internal_images,
    _convert_uploaded_images_to_jpg,
    _extract_images_zip,
    _normalize_uploaded_image_stem,
)
from web.printbook.services import extract_print_illustrations


class EpubImageIntegrityTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_repeated_placeholder_insertion_preserves_existing_positions_and_bytes(self):
        for content in (
            b'# Chapter 1\r\n\r\nBefore.\r\n\r\n![](assets/images/03.jpg)\r\n\r\nAfter.\r\n',
            b'# Chapter 1\n\nBefore.\n\n{{IMAGE:CH01:02}}\n\nAfter.\n',
            b'# Chapter 1\n\nBefore.\n\n![Scene](other/image.png)\n\nAfter.\n',
        ):
            with self.subTest(content=content):
                manuscript = self.root / 'BOOK.PRE_EDITION.md'
                manuscript.write_bytes(content)
                md_transform.insert_image_placeholders(manuscript)
                self.assertEqual(manuscript.read_bytes(), content)

    def test_missing_number_does_not_consume_next_chapter_image(self):
        self.assertIsNone(md_transform._pick_image_for_placeholder([Path('02.jpg')], set(), 1, 1))

    def test_slot_does_not_consume_other_slot(self):
        self.assertIsNone(md_transform._pick_image_for_placeholder([Path('ch01_02.jpg')], set(), 1, 1))

    def test_ambiguous_number_is_not_resolved_by_filename_sort(self):
        self.assertIsNone(md_transform._pick_image_for_placeholder([Path('01.jpg'), Path('01.png')], set(), 1, 1))

    def test_numbered_matches_are_independent_of_upload_order(self):
        images = [Path('10.jpg'), Path('02.jpg'), Path('01.jpg')]
        self.assertEqual(md_transform._pick_image_for_placeholder(images, set(), 2, 1), Path('02.jpg'))

    def test_upload_normalization_preserves_slot(self):
        self.assertEqual(_normalize_uploaded_image_stem('ch02_03.png'), 'ch02_03')
        self.assertEqual(_normalize_uploaded_image_stem('02_10.png'), 'ch02_10')

    def test_duplicate_upload_is_rejected_before_writing(self):
        with self.assertRaises(RuntimeError):
            _convert_uploaded_images_to_jpg(
                [SimpleNamespace(name='01.png'), SimpleNamespace(name='ch01_01.jpg')], self.root
            )
        self.assertEqual(list(self.root.iterdir()), [])

    def test_zip_duplicate_number_cannot_overwrite_existing_gallery(self):
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, 'w') as archive:
            archive.writestr('first/01.png', b'first')
            archive.writestr('second/01.png', b'second')
        existing = self.root / '01.png'
        existing.write_bytes(b'approved')
        archive_bytes.seek(0)
        with self.assertRaises(RuntimeError):
            _extract_images_zip(archive_bytes, self.root)
        self.assertEqual(existing.read_bytes(), b'approved')

    def test_valid_zip_keeps_numbering_independent_of_archive_order(self):
        from PIL import Image

        image_bytes = io.BytesIO()
        Image.new('RGB', (2, 2), 'red').save(image_bytes, format='PNG')
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, 'w') as archive:
            archive.writestr('folder/ch02_03.png', image_bytes.getvalue())
            archive.writestr('folder/01.png', image_bytes.getvalue())
        gallery = self.root / 'gallery'
        gallery.mkdir()
        (gallery / 'previous.jpg').write_bytes(b'previous-gallery')
        archive_bytes.seek(0)
        self.assertEqual(_extract_images_zip(archive_bytes, gallery), 2)
        self.assertEqual(sorted(path.name for path in gallery.iterdir()), ['01.png', 'ch02_03.png'])

    def test_corrupt_zip_image_keeps_previous_gallery(self):
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, 'w') as archive:
            archive.writestr('02.png', b'not-an-image')
        existing = self.root / '01.png'
        existing.write_bytes(b'approved')
        archive_bytes.seek(0)
        with self.assertRaises(RuntimeError):
            _extract_images_zip(archive_bytes, self.root)
        self.assertEqual(existing.read_bytes(), b'approved')
        self.assertEqual(list(self.root.iterdir()), [existing])

    def test_gallery_invalidation_preserves_embedded_image_files(self):
        asset = self.root / 'ch01_01_01.jpg'
        asset.write_bytes(b'approved-embedded-image')
        (self.root / 'images_map.json').write_text('{}')
        with patch('pipeline.views._consolidated_images_dir_for_edition', return_value=self.root):
            _clear_consolidated_images_for_edition(None)
        self.assertEqual(asset.read_bytes(), b'approved-embedded-image')
        self.assertFalse((self.root / 'images_map.json').exists())

    def test_consolidation_preserves_images_already_referenced_in_text(self):
        from PIL import Image

        gallery = self.root / 'gallery'
        gallery.mkdir()
        Image.new('RGB', (2, 2), 'red').save(gallery / '02.jpg')
        destination = self.root / 'assets'
        destination.mkdir()
        embedded = destination / 'ch01_01_01.jpg'
        embedded.write_bytes(b'approved-image')
        _consolidate_internal_images(gallery, destination)
        self.assertEqual(embedded.read_bytes(), b'approved-image')
        self.assertTrue((destination / 'ch02_01.jpg').exists())

    def test_apply_preserves_frontmatter_and_text_bytes_outside_anchor(self):
        manuscript = self.root / 'BOOK.PRE_EDITION.md'
        gallery = self.root / 'gallery'
        gallery.mkdir()
        (gallery / '02.jpg').write_bytes(b'image-two')
        original = b'---\r\ntitle: Preserved\r\ndrive_file_id: synthetic-drive-id\r\n---\r\n# Chapter 2\r\n\r\nBefore.\r\n{{IMAGE:CH02:01}}\r\nAfter.\r\n'
        manuscript.write_bytes(original)
        result = md_transform.apply_images_to_pre_edition(manuscript, gallery)
        self.assertEqual(result['inserted'], 1)
        expected = original.replace(b'{{IMAGE:CH02:01}}', b'![](assets/images/ch02_01_02.jpg)')
        self.assertEqual(manuscript.read_bytes(), expected)
        md_transform.apply_images_to_pre_edition(manuscript, gallery)
        self.assertEqual(manuscript.read_bytes(), expected)

    def test_consolidation_rejects_duplicate_slot_without_renumbering(self):
        gallery = self.root / 'gallery'
        gallery.mkdir()
        (gallery / '01.jpg').write_bytes(b'one')
        (gallery / 'ch01_01.png').write_bytes(b'two')
        with self.assertRaises(RuntimeError):
            _consolidate_internal_images(gallery, self.root / 'consolidated')
        self.assertFalse((self.root / 'consolidated').exists())

    def test_consolidation_rejects_unnumbered_before_touching_previous_output(self):
        gallery = self.root / 'gallery'
        gallery.mkdir()
        (gallery / 'scene.jpg').write_bytes(b'not-read')
        destination = self.root / 'consolidated'
        destination.mkdir()
        previous = destination / 'images_map.json'
        previous.write_text('previous mapping')
        with self.assertRaises(RuntimeError):
            _consolidate_internal_images(gallery, destination)
        self.assertEqual(previous.read_text(), 'previous mapping')

    def renderer(self):
        renderer = object.__new__(EditionRenderer)
        renderer.build_dir = self.root
        renderer._metadata = {}
        renderer.warnings = []
        renderer.theme = json.loads((THEME_ROOT / 'theme.json').read_text())
        return renderer

    def test_default_chapter_has_image_and_text_without_extra_page(self):
        renderer = self.renderer()
        chapter = RenderUnit('chapter', 'chapter-001', 'Arrival', 'Chapter 1', 'chapter_001.xhtml',
                             '<figure><img src="../images/01.jpg" alt="Scene"/></figure><p>Whole text.</p>')
        with patch.object(renderer, '_write_xhtml') as write:
            navigation = renderer._render_units(self.root, [chapter])
        self.assertEqual(write.call_count, 1)
        self.assertEqual(navigation[0][0], 'chapter_001.xhtml')
        soup = BeautifulSoup(write.call_args.args[-1], 'html.parser')
        self.assertEqual(soup.select_one('.chapter-body p').text, 'Whole text.')
        self.assertIsNotNone(soup.select_one('.chapter-body img'))

    def test_unnumbered_gallery_is_not_silently_assigned(self):
        renderer = self.renderer()
        image = self.root / 'scene.jpg'
        image.write_bytes(b'scene')
        chapter = RenderUnit('chapter', 'chapter-001', 'Arrival', 'Chapter 1', 'chapter_001.xhtml', '<p>Text.</p>')
        with patch.object(renderer, '_saved_illustrations', return_value=[image]):
            with self.assertRaises(ValueError):
                renderer._attach_saved_illustrations([chapter])
        self.assertEqual(chapter.body_html, '<p>Text.</p>')

    def test_unresolved_placeholder_blocks_render(self):
        with self.assertRaises(ValueError):
            self.renderer()._clean_source('# Chapter 1\n\n{{IMAGE:CH01:01}}\n\nText.')

    def test_structure_does_not_add_opening_image_when_source_has_positioned_image(self):
        renderer = self.renderer()
        (self.root / 'chapter_structure.json').write_text(json.dumps({
            'schema': 'gaiden_chapter_structure_v1',
            'chapters': [{'anchor': '# Chapter 1', 'title': 'One', 'image': '01.jpg'}],
        }))
        source = '# Chapter 1\n\nBefore.\n\n![](positioned.jpg)\n\nAfter.\n'
        transformed = renderer._apply_chapter_structure(source)
        self.assertEqual(transformed.count('![]('), 1)
        self.assertIn('Before.\n\n![](positioned.jpg)\n\nAfter.', transformed)
        self.assertNotIn('(01.jpg)', transformed)

    def test_gallery_does_not_move_an_already_embedded_image_to_its_numeric_chapter(self):
        renderer = self.renderer()
        image = self.root / '02.jpg'
        image.write_bytes(b'approved-scene')
        embedded = self.root / 'embedded.jpg'
        embedded.write_bytes(image.read_bytes())
        positioned = '<p>Before.</p><figure><img src="embedded.jpg"/></figure><p>After.</p>'
        chapters = [RenderUnit('chapter', 'chapter-001', 'One', 'Chapter 1', 'chapter_001.xhtml', positioned),
                    RenderUnit('chapter', 'chapter-002', 'Two', 'Chapter 2', 'chapter_002.xhtml', '<p>Second.</p>')]
        with patch.object(renderer, '_saved_illustrations', return_value=[image]):
            renderer._attach_saved_illustrations(chapters)
        self.assertEqual(chapters[0].body_html, positioned)
        self.assertNotIn('<img', chapters[1].body_html)

    def test_legacy_separate_page_flag_does_not_split_chapter(self):
        renderer = self.renderer()
        renderer.theme['illustration_opening_separate_xhtml'] = True
        unit = RenderUnit('chapter', 'chapter-001', 'One', 'Chapter 1', 'chapter_001.xhtml', '<figure><img src="../images/01.jpg"/></figure><p>Text.</p>')
        with patch.object(renderer, '_write_xhtml') as write:
            renderer._render_units(self.root, [unit])
        self.assertEqual(write.call_count, 1)
        self.assertEqual(unit.opening_filename, '')

    def test_principal_css_is_used_unchanged_with_default_theme(self):
        self.assertEqual(self.renderer()._theme_css(), (THEME_ROOT / 'styles/gaiden-premium.css').read_text())

    def test_print_reads_inline_figure_without_requiring_opening_page_or_css(self):
        epub = self.root / 'approved.epub'
        with zipfile.ZipFile(epub, 'w') as archive:
            archive.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="EPUB/book.opf"/></rootfiles></container>')
            archive.writestr('EPUB/book.opf', '<package><manifest><item id="one" href="text/chapter_001.xhtml" media-type="application/xhtml+xml"/><item id="image" href="images/09.jpg" media-type="image/jpeg"/></manifest><spine><itemref idref="one"/></spine></package>')
            archive.writestr('EPUB/text/chapter_001.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><section epub:type="chapter"><p>Before.</p><figure><img src="../images/09.jpg"/></figure><p>After.</p></section></body></html>')
            archive.writestr('EPUB/images/09.jpg', b'approved image bytes')
            archive.writestr('EPUB/styles/main.css', 'EPUB typography only')
        original = epub.read_bytes()
        source = SimpleNamespace(epub_path=str(epub), _safe_existing_path=lambda *args, **kwargs: epub)
        with patch('web.printbook.services.enhance_print_image', side_effect=lambda original, target, profile: shutil.copyfile(original, target)):
            manifest, files = extract_print_illustrations(source, self.root / 'print')
        self.assertEqual(set(manifest), {1})
        self.assertEqual(files['illustration_001'].read_bytes(), b'approved image bytes')
        self.assertFalse(list((self.root / 'print').rglob('*.css')))
        self.assertEqual(epub.read_bytes(), original)
