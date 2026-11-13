from pathlib import Path

from django.test import SimpleTestCase


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "pipeline"


class OptionalImagesControlsTests(SimpleTestCase):
    def test_post_intake_marks_internal_images_as_optional(self):
        template = (TEMPLATE_DIR / "post_intake_workflow.html").read_text(encoding="utf-8")

        self.assertIn("Sem imagens internas · etapa opcional", template)
        self.assertIn("isso não bloqueia os checks nem a geração do livro", template)
        self.assertNotIn("gallery_count %}ok{% else %}warn", template)

    def test_post_intake_requires_button_click_for_cover_upload(self):
        template = (TEMPLATE_DIR / "post_intake_workflow.html").read_text(encoding="utf-8")

        self.assertIn("Nenhum envio ou check é executado automaticamente", template)
        self.assertIn("clique em Salvar capa", template)
        self.assertNotIn("requestSubmit", template)

    def test_writer_intake_marks_internal_images_as_optional(self):
        template = (TEMPLATE_DIR / "writer_intake_assets.html").read_text(encoding="utf-8")

        self.assertIn("Imagens internas · opcional", template)
        self.assertIn("sem imagens internas", template)
        self.assertIn("só ocorre ao clicar", template)
