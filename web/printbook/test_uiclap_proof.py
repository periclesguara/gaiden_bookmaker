from decimal import Decimal
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import subprocess
import shutil
import unittest
from unittest.mock import Mock

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from gaiden.print_engine.specs import PrintProvider, PrintSpec
from web.printbook.forms import CoverDesignForm
from web.printbook.services import design_cover, estimated_cover_geometry, estimated_spine_width


class UiclapProofTests(SimpleTestCase):
    def setUp(self):
        self.edition = SimpleNamespace(platform='uiclap', paper='white', name='Book')

    def test_short_books_have_zero_spine_and_five_mm_bleed(self):
        for pages in (29, 30, 59):
            self.assertEqual(estimated_spine_width(self.edition, pages), 0)
        self.assertGreater(estimated_spine_width(self.edition, 60), 0)
        with patch('web.printbook.services.spec_for', return_value=PrintSpec(provider=PrintProvider.UICLAP)):
            geometry = estimated_cover_geometry(self.edition, 30)
        self.assertEqual(Decimal(geometry['spine_width_in']), 0)
        self.assertEqual(Decimal(geometry['bleed_in']), Decimal('5') / Decimal('25.4'))
        self.assertFalse(geometry['spine_text_allowed'])

    def test_enhancement_profile_is_available_and_zero_spine_is_accepted(self):
        form = CoverDesignForm()
        self.assertIn('uiclap_300', dict(form.fields['enhancement_profile'].choices))
        self.assertEqual(form.fields['spine'].clean('0'), Decimal('0'))

    def test_stapled_cover_cannot_be_marked_final_or_accept_spine_upload(self):
        args = dict(layout='classic', title='Title', subtitle='', author='Author', back_text='Back', spine_text='', background_color='#000000', text_color='#FFFFFF', accent_color='#FFFFFF')
        with patch('web.printbook.services.guard'), patch('web.printbook.services.latest', return_value=SimpleNamespace(page_count=30)), patch('web.printbook.services.CoverBuild') as covers, patch('web.printbook.services.load_cover_design', return_value={}), patch('web.printbook.services.new_build') as new_build:
            covers.objects.filter.return_value.first.return_value = None
            with self.assertRaisesMessage(ValueError, 'permanece como rascunho'):
                design_cover(self.edition, save_mode='final', template=object(), **args)
            with self.assertRaisesMessage(ValueError, 'não usa lombada'):
                design_cover(self.edition, save_mode='draft', spine_image=object(), **args)
            new_build.assert_not_called()

    def test_stapled_draft_preserves_original_resolution_finding_and_has_no_spine(self):
        original = BytesIO()
        Image.new("RGB", (100, 150), "gray").save(original, format="PNG")
        upload = SimpleUploadedFile("cover.png", original.getvalue(), content_type="image/png")
        edition = SimpleNamespace(platform="uiclap", paper="white", name="Book", barcode_mode="provider", save=Mock())
        build = SimpleNamespace(save=Mock())
        interior = SimpleNamespace(page_count=30)
        geometry_seen = {}
        def new_build(_model, _edition, *, interior, geometry):
            geometry_seen.update(geometry)
            return build
        def compile_cover(_args, **kwargs):
            (kwargs["cwd"] / "COVER.pdf").write_bytes(b"%PDF-proof")
            return subprocess.CompletedProcess(_args, 0)
        with TemporaryDirectory() as directory, patch("web.printbook.services.guard"), patch("web.printbook.services.latest", return_value=interior), patch("web.printbook.services.CoverBuild") as covers, patch("web.printbook.services.load_cover_design", return_value={}), patch("web.printbook.services.new_build", side_effect=new_build), patch("web.printbook.services.root_for", return_value=Path(directory)), patch("web.printbook.services.spec_for", return_value=PrintSpec(provider=PrintProvider.UICLAP, trim_width_in="2", trim_height_in="3")), patch("web.printbook.services.subprocess.run", side_effect=compile_cover), patch("web.printbook.services.inspect_pdf_page_count", return_value=1), patch("web.printbook.services.tool_version", return_value="test"), patch("web.printbook.services.invalidate_after_cover"):
            covers.objects.filter.return_value.first.return_value = None
            result = design_cover(edition, layout="classic", title="Title", subtitle="", author="Author", back_text="Back", spine_text="Old spine", background_color="#000000", text_color="#FFFFFF", accent_color="#FFFFFF", enhancement_profile="uiclap_300", cover_image=upload, save_mode="draft")
            design = json.loads(Path(result.files["design"]["path"]).read_text())
            tex = Path(result.files["tex"]["path"]).read_text()
            self.assertEqual(result.status, "DRAFT")
            self.assertEqual(Decimal(geometry_seen["spine_width_in"]), 0)
            self.assertNotIn("Old spine", tex)
            self.assertEqual(design["front_image_quality"]["original_width_px"], 100)
            self.assertEqual(design["image_enhancement"]["resolution_note"], "interpolated")
            self.assertTrue(any(item["code"] == "cover.front_image_resolution" and item["severity"] == "BLOCKER" for item in result.findings))
            self.assertTrue(any(item["code"] == "cover.uiclap_stapled_proof" for item in result.findings))

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise the preview script")
    def test_stapled_preview_keeps_both_cover_panels_in_grid(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text(encoding="utf-8")
        script = "const noSpine =" + template.split("    const noSpine =", 1)[1].split("    preview.style.backgroundColor", 1)[0]
        harness = r"""
const simulator = {dataset: {noSpine: 'yes'}};
const preview = {style: {}};
const spine = 0;
const live = {style: {}}, book = {style: {}};
const all = selector => selector === '.cover-live-spine' ? [live] : [book];
""" + script + r"""
if (preview.style.gridTemplateColumns !== '1fr 0fr 1fr') process.exit(1);
if (live.style.visibility !== 'hidden' || live.style.display === 'none') process.exit(1);
if (book.style.display !== 'none') process.exit(1);
"""
        subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)
