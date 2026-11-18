from django.test import RequestFactory, SimpleTestCase, override_settings
from django.template.loader import render_to_string
from bs4 import BeautifulSoup
from pathlib import Path
import shutil
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from web.printbook.forms import CoverDesignForm
from web.printbook.services import preflight, quality_readiness, workflow_steps


class CoverEditorControlTests(SimpleTestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise the preview script")
    def test_saved_enhancement_uses_original_pixels_but_upload_uses_new_pixels(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text()
        script = "const capture =" + template.split("    const capture =", 1)[1].split("    if (existing)", 1)[0]
        harness = r'''
const panel = 'front', dimensions = {};
const document = {getElementById: () => ({textContent: JSON.stringify({front_image_quality: {original_width_px: 1024, original_height_px: 1536}})})};
const updateImageQuality = () => {};
''' + script + r'''
capture({naturalWidth: 1949, naturalHeight: 2924}, true);
if (dimensions.front.width !== 1024 || dimensions.front.height !== 1536) process.exit(1);
capture({naturalWidth: 4000, naturalHeight: 6000});
if (dimensions.front.width !== 4000 || dimensions.front.height !== 6000) process.exit(2);
'''
        subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)

    @override_settings(PRINTBOOK_VALIDATOR="")
    def test_quality_control_remains_accessible_with_draft_cover(self):
        edition = SimpleNamespace(pk=6, name="Edition", source_id=1, get_platform_display=lambda: "Platform", interior_approval={"build_id": 23}, cover_approval={}, visual_review_approved_at=None, edition_approved_at=None)
        interior = SimpleNamespace(id=23)
        checks = quality_readiness(edition, interior, None)
        self.assertTrue(checks[0]["ok"])
        self.assertFalse(checks[1]["ok"])
        self.assertFalse(checks[2]["ok"])
        self.assertIn("não configurado", checks[3]["message"])
        request = RequestFactory().get("/printbook/6/preflight/")
        page = render_to_string("printbook/preflight.html", {"edition": edition, "quality_readiness": checks, "report": None, "workflow_steps": []}, request=request)
        soup = BeautifulSoup(page, "html.parser")
        button = soup.find("button", string="Executar controle de qualidade")
        self.assertIsNotNone(button)
        self.assertFalse(button.has_attr("disabled"))
        self.assertEqual(soup.find("a", string="Autoajustar capa")["href"], "/printbook/6/cover/?tab=montage&autofit=1")
        self.assertTrue(soup.find("button", string="Confirmar revisão visual").has_attr("disabled"))

    def test_draft_cover_cannot_create_a_production_preflight(self):
        edition = SimpleNamespace()
        with patch("web.printbook.services.guard"), patch("web.printbook.services.InteriorBuild") as interior, patch("web.printbook.services.CoverBuild") as cover, patch("web.printbook.services.new_build") as new_build:
            interior.objects.filter.return_value.first.return_value = SimpleNamespace(id=23)
            cover.objects.filter.return_value.first.return_value = None
            with self.assertRaisesMessage(ValueError, "A capa ainda é uma prova"):
                preflight(edition)
            new_build.assert_not_called()

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise the preview script")
    def test_quality_link_opens_montage_and_runs_auto_fit_once(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text(encoding="utf-8")
        script = "const autoFitUrl =" + template.split("  const autoFitUrl =", 1)[1].split("}());", 1)[0]
        harness = r'''
let clicked=0, tab='';
const window = {location: {href: 'http://localhost/printbook/6/cover/?tab=montage&autofit=1'}, history: {state: null, replaceState: (_, __, url) => {window.location.href = url.href;}}};
const activateCoverTab = value => {tab = value;};
const autoFitButton = {click: () => {clicked++;}};
''' + script + r'''
if (clicked !== 1 || tab !== 'montage' || new URL(window.location.href).searchParams.has('autofit')) process.exit(1);
'''
        subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise the preview script")
    def test_auto_fit_calculates_non_overlapping_blocks_and_rejects_overflow(self):
        script = Path(__file__).parent / "static/printbook/cover_autofit.js"
        harness = r'''
require(process.argv[1]);
const fit = globalThis.fitCoverBlocks;
const blocks = [{height: 18, minimum: 8, maximum: 70, preferred: 10}, {height: 14, minimum: 10, maximum: 80, preferred: 15}];
const result = fit(blocks, 90, 2);
if (!result || result[0] !== 10 || result[1] < result[0] + 20) process.exit(1);
if (fit([{height: 90, minimum: 8, maximum: 90, preferred: 8}], 90, 2) !== null) process.exit(1);
if (fit([{height: 0, minimum: 8, maximum: 90, preferred: 8}], 90, 2) !== null) process.exit(1);
const safeResult = fit([{height: 10, minimum: 0, maximum: 90, preferred: 0}], 90, 2, 9);
if (!safeResult || safeResult[0] !== 9) process.exit(1);
'''
        subprocess.run(["node", "-e", harness, str(script)], check=True, capture_output=True, text=True)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise the preview script")
    def test_auto_fit_updates_positions_and_rolls_back_when_text_cannot_fit(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text(encoding="utf-8")
        script = "const autoFitButton =" + template.split("  const autoFitButton =", 1)[1].split("  form.querySelectorAll('input, textarea, select')", 1)[0]
        library = Path(__file__).parent / "static/printbook/cover_autofit.js"
        harness = r'''
require(process.argv[1]);
const window = {fitCoverBlocks: globalThis.fitCoverBlocks};
let click, oversized = false;
const button = {addEventListener: (_, callback) => {click = callback;}};
const status = {textContent: ''};
const panel = {clientHeight: 600};
const node = {clientWidth: 200, scrollWidth: 200, get scrollHeight() {return oversized ? 5000 : 30;}};
const field = (value, min='', max='') => ({value, min, max});
const form = {elements: {add_front_text: {checked: true}, front_text_scale_percent: field('80'),
 title: field('Title'), author: field('Author'), subtitle: field(''), language: field(''), publisher: field(''),
 front_title_x_percent: field('20'), front_title_y_percent: field('20','8','70'), front_title_font_size_pt: field('28','12','72'),
 front_author_x_percent: field('20'), front_author_y_percent: field('84','45','92'), front_author_font_size_pt: field('8','8','36'),
 back_heading: field(''), back_quote: field(''), back_text: field('Back cover copy'), back_bio: field(''), back_footer: field(''),
 back_element_body_x_percent: field('25'), back_element_body_y_percent: field('80','0','90'), back_element_body_font_size_pt: field('10','9','24')}};
const value = name => (form.elements[name] && form.elements[name].value) || '';
const document = {querySelector: selector => selector === '.cover-live-front' || selector === '.cover-live-back' ? panel : selector.startsWith('.cover-live-front [') || selector.startsWith('.cover-live-back [') ? node : null,
 getElementById: id => id === 'auto-fit-cover' ? button : status};
const simulator = {dataset: {barcodeMode: 'provider', spineAllowed: 'yes'}};
const trimHeight = 9, panelHeight = 9.25;
const snapshot = () => Object.fromEntries(Object.entries(form.elements).filter(([_, field]) => field.value !== undefined).map(([key, field]) => [key, field.value]));
const restore = state => Object.entries(state).forEach(([key, value]) => {form.elements[key].value = value;});
let saved = 0; const pushHistory = () => {saved++;}; const update = () => {};
''' + script + r'''
(async () => {
 await click();
 if (form.elements.front_title_x_percent.value !== '50' || form.elements.front_text_scale_percent.value !== '100' || saved !== 2) throw Error('fit: ' + status.textContent + ' / ' + saved);
 if (form.elements.back_element_body_x_percent.value !== '50' || Number(form.elements.back_element_body_y_percent.value) < 10 || Number(form.elements.back_element_body_y_percent.value) > 72) throw Error('back safety bounds');
 if (Number(form.elements.front_author_font_size_pt.value) !== 9) throw Error('minimum font size not applied');
 if (!status.textContent.includes('Textos organizados')) throw Error('status: ' + status.textContent);
 const before = JSON.stringify(snapshot()); oversized = true;
 await click();
 if (JSON.stringify(snapshot()) !== before || !status.textContent.includes('não couberam')) throw Error('rollback: ' + status.textContent);
 oversized = false;
 Object.assign(form.elements, {spine_title: field('Livro'), spine_author: field(''), spine_publisher: field(''), spine: field('0.5'), spine_edge_safe_mm: field('1.6'), spine_letter_spacing_pt: field('0'), spine_font_size_pt: field('8'), spine_text_x_percent: field('30'), spine_text_y_percent: field('70'), spine_offset_x_mm: field('2'), spine_offset_y_mm: field('1')});
 await click();
 if (Number(form.elements.spine_font_size_pt.value) !== 9 || form.elements.spine_text_y_percent.value !== '50' || form.elements.spine_offset_x_mm.value !== '0') throw Error('spine was not fitted');
 simulator.dataset.spineAllowed = 'no';
 await click();
 if (!status.textContent.includes('não permite texto na lombada')) throw Error('missing provider restriction');
})().catch(error => {console.error(error); process.exit(1);});
'''
        result = subprocess.run(["node", "-e", harness, str(library)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise the preview script")
    def test_panel_coordinates_and_font_size_match_print_geometry(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text(encoding="utf-8")
        script = "const inches =" + template.split("  const inches =", 1)[1].split("  const previewFontFamilies", 1)[0]
        harness = r'''
const simulator = {dataset: {trimWidth: '6', trimHeight: '9', bleed: '0.125'}};
''' + script + r'''
const almost = (a, b) => Math.abs(a-b) < 0.00001;
if (!almost(trimPercent(panelPercent(42, 'x'), 'x'), 42)) process.exit(1);
if (!almost(trimPercent(panelPercent(75, 'y'), 'y'), 75)) process.exit(1);
if (!almost(textWidthPercent, 80) || textWidthPercent >= 82) process.exit(1);
const panel = {clientHeight: 333};
if (!almost(parseFloat(previewSize({closest: () => panel}, 18)), 9)) process.exit(1);
'''
        subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise the preview script")
    def test_front_montage_uses_absolute_positioning_and_scaled_size(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text(encoding="utf-8")
        script = "[['title'," + template.split("    [['title',", 1)[1].split("    const fontPreviewCopies", 1)[0]
        harness = r'''
const titleX=50,titleY=20,subtitleX=50,subtitleY=34,authorX=50,authorY=84,languageX=50,languageY=84,publisherX=50,publisherY=89,frontScale=1;
const nodes = Array.from({length: 5}, () => ({style: {}}));
let i=0;
const all = () => [nodes[i++]];
const textWidthPercent = 86;
const panelPercent = (n) => n+1;
const previewSize = (_, n) => `${n/2}px`;
const value = () => '';
''' + script + r'''
if (nodes.some(node => node.style.position !== 'absolute' || node.style.width !== '86%')) process.exit(1);
if (nodes[0].style.top !== '21%' || nodes[0].style.fontSize !== '14px') process.exit(1);
'''
        subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise the preview script")
    def test_active_tab_is_preserved_in_url_for_reload(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text(encoding="utf-8")
        script = "const activateCoverTab =" + template.split("  const activateCoverTab =", 1)[1].split("  all('[data-step-cover-tab]').forEach((link) => link.addEventListener", 1)[0]
        harness = r'''
const window = {location: {href: 'http://localhost/printbook/6/cover/?saved=draft#editor'}, history: {state: null, replaceState: (_, __, url) => {window.location.href = url.href;}}};
const all = () => [];
const form = {closest: () => ({classList: {toggle: () => {}}})};
const update = () => {};
let selectedPanel = 'front';
''' + script + r'''
activateCoverTab('spine');
let url = new URL(window.location.href);
if (url.searchParams.get('tab') !== 'spine' || url.searchParams.get('saved') !== 'draft' || url.hash !== '#editor') process.exit(1);
activateCoverTab('montage');
url = new URL(window.location.href);
if (url.searchParams.get('tab') !== 'montage') process.exit(1);
'''
        subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise the preview script")
    def test_rotate_spine_button_changes_direction_and_dispatches_change(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text(encoding="utf-8")
        script = "const rotateSpine =" + template.split("  const rotateSpine =", 1)[1].split("  const applyLayoutButton", 1)[0]
        harness = r'''
let click; let changes = 0;
const button = {addEventListener: (_, callback) => {click = callback;}};
const document = {querySelector: () => button};
const form = {elements: {spine_rotation: {value: '-90', dispatchEvent: () => {changes++;}}}};
class Event { constructor(type) {this.type = type;} }
''' + script + r'''
click(); if (form.elements.spine_rotation.value !== '90' || changes !== 1) process.exit(1);
click(); if (form.elements.spine_rotation.value !== '-90' || changes !== 2) process.exit(1);
'''
        subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)

    def test_new_spine_defaults_to_top_to_bottom(self):
        form = CoverDesignForm()
        self.assertEqual(form.fields['spine_rotation'].initial, '-90')
        self.assertEqual(form.fields['spine_rotation'].choices[0], ('-90', 'De cima para baixo'))

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise the preview script")
    def test_spine_preview_rotates_horizontal_text_in_pdf_reading_direction(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text(encoding="utf-8")
        script = "const spineCopy =" + template.split("    const spineCopy =", 1)[1].split("    const artOnly =", 1)[0]
        harness = r'''
let direction = '90';
const nodes = [{style: {}}, {style: {}}, {style: {}}];
const all = () => nodes;
const form = {elements: {spine_title: {}}};
const simulator = {dataset: {spine: '0.5'}};
const panelHeight = 9.25;
const value = name => name === 'spine_title' ? 'Livro' : name === 'spine_rotation' ? direction : '';
const previewFont = font => font;
const previewSize = (_, size) => `${size}pt`;
const render = () => {
''' + script + r'''
};
render();
if (nodes.some(node => node.style.writingMode !== 'horizontal-tb' || !node.style.transform.includes('rotate(-90deg)'))) process.exit(1);
direction = '-90'; render();
if (nodes.some(node => !node.style.transform.includes('rotate(90deg)'))) process.exit(1);
'''
        subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)

    def test_clearing_spine_title_does_not_restore_legacy_text(self):
        form = CoverDesignForm(data={"spine_title": "", "spine_text": "Título antigo"})
        form.is_valid()
        self.assertEqual(form.cleaned_data["spine_title"], "")

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise the preview script")
    def test_preview_ignores_legacy_title_after_user_clears_it(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text(encoding="utf-8")
        script = "const spineCopy =" + template.split("    const spineCopy =", 1)[1].split("    const artOnly =", 1)[0]
        harness = r'''
const nodes = [{style: {}}];
const all = () => nodes;
const form = {elements: {spine_title: {}}};
const simulator = {dataset: {spine: '0.5'}};
const panelHeight = 9.25;
const value = name => name === 'spine_text' ? 'Título antigo' : '';
const previewFont = font => font;
const previewSize = (_, size) => `${size}pt`;
''' + script + r'''
if (nodes[0].textContent !== '' || nodes[0].hidden !== true) process.exit(1);
'''
        subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)

    def test_missing_spine_title_uses_legacy_text(self):
        form = CoverDesignForm(data={"spine_text": "Título antigo"})
        form.is_valid()
        self.assertEqual(form.cleaned_data["spine_title"], "Título antigo")

    def test_workflow_orders_cover_panels_before_preflight_and_export(self):
        edition = SimpleNamespace(state="CONFIGURED", interior_approval={}, cover_approval={}, edition_approved_at=None)
        with patch("web.printbook.services.InteriorBuild") as interior, patch("web.printbook.services.CoverBuild") as cover, patch("web.printbook.services.PreflightReport") as report, patch("web.printbook.services.PrintExport") as export:
            for model in (interior, cover, report):
                model.objects.filter.return_value.first.return_value = None
            export.objects.filter.return_value.exists.return_value = False
            steps = workflow_steps(edition)
        self.assertEqual([step["label"] for step in steps], ["Configuração", "Miolo", "Capa", "Contracapa", "Lombada", "Preflight", "Exportação"])
        self.assertEqual([step.get("cover_tab") for step in steps[2:5]], ["front", "back", "spine"])
        self.assertEqual(len({step["status"] for step in steps[2:5]}), 1)

    def test_draft_cover_is_in_editing_and_back_tab_remains_accessible(self):
        edition = SimpleNamespace(state="CONFIGURED", interior_approval={}, cover_approval={}, edition_approved_at=None)
        with patch("web.printbook.services.InteriorBuild") as interior, patch("web.printbook.services.CoverBuild") as cover, patch("web.printbook.services.PreflightReport") as report, patch("web.printbook.services.PrintExport") as export:
            interior.objects.filter.return_value.first.return_value = None
            report.objects.filter.return_value.first.return_value = None
            cover.objects.filter.return_value.first.side_effect = [None, SimpleNamespace(status="DRAFT")]
            export.objects.filter.return_value.exists.return_value = False
            steps = workflow_steps(edition)
        self.assertEqual(steps[3]["status"], "progress")
        self.assertEqual(steps[3]["status_label"], "Em edição · capa completa")
        self.assertEqual(steps[3]["url_name"], "printbook:cover")
        self.assertEqual(steps[3]["cover_tab"], "back")

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to exercise the preview script")
    def test_empty_back_text_is_hidden_in_both_previews(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text(encoding="utf-8")
        script = template.split("    const backCopies =", 1)[1].split("    const spineCopy =", 1)[0]
        script = "const backCopies =" + script
        harness = r'''
const values = {back_heading: '  ', back_quote: '', back_text: 'Texto', back_bio: '', back_footer: ''};
const nodes = ['heading', 'quote', 'body', 'bio', 'footer'].flatMap(key => [
  {dataset: {backElement: key}, style: {}, closest: () => ({})},
  {dataset: {backElement: key}, style: {}, closest: () => null}
]);
const all = () => nodes;
const value = name => values[name] || '';
const previewFont = name => name;
const applyShape = () => {};
const previewSize = (_, size) => `${size}pt`;
const textWidthPercent = 80;
const panelPercent = n => n;
'''
        assertions = r'''
if (nodes.some(node => node.hidden !== (node.dataset.backElement !== 'body'))) process.exit(1);
'''
        subprocess.run(["node", "-e", harness + script + assertions], check=True, capture_output=True, text=True)

    def test_back_workspace_displays_every_back_text_field(self):
        template = (Path(__file__).parent / "templates/printbook/cover.html").read_text(encoding="utf-8")
        stage = template.split('data-layout-stage="back"', 1)[1].split("</div>", 1)[0]
        self.assertIn('data-back-element="{{ element.key }}"', stage)
        self.assertIn('data-x-field="{{ element.prefix }}_x_percent"', stage)
        self.assertIn('data-y-field="{{ element.prefix }}_y_percent"', stage)

    def test_each_back_element_has_independent_controls(self):
        form = CoverDesignForm()
        for name in ("heading", "quote", "body", "bio", "footer"):
            for option in ("font_family", "font_size_pt", "weight", "style", "letter_spacing_pt", "color", "x_percent", "y_percent"):
                self.assertIn(f"back_element_{name}_{option}", form.fields)

    def test_back_and_spine_use_front_font_catalog_and_color_inputs(self):
        form = CoverDesignForm()
        self.assertEqual(
            list(form.fields["cover_font_family"].choices),
            list(form.fields["front_title_font_family"].choices),
        )
        self.assertEqual(
            list(form.fields["spine_font_family"].choices),
            list(form.fields["front_title_font_family"].choices),
        )
        for name in ("back_background_color", "back_text_color", "back_accent_color", "spine_color"):
            self.assertEqual(form.fields[name].widget.input_type, "color")
            self.assertEqual(form.fields[name].clean("#2563EB"), "#2563EB")
