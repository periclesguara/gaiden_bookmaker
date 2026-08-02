from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase

from editorial.frontmatter import build_frontmatter_files
from editorial.models import Contributor, Language, Seal, Work, Edition
from pipeline.models import BookEditionTemplate


class FrontmatterLanguageExportTests(TestCase):
    def test_export_uses_edition_fk_language_before_legacy_language_code(self):
        en = Language.objects.create(code="en", name="English", native_name="English")
        fr = Language.objects.create(code="fr", name="French", native_name="Francais")
        seal = Seal.objects.create(slug="rinobooks", name="RinoBooks")
        author = Contributor.objects.create(name="Epictetus")
        work = Work.objects.create(
            code="book_lang_guard",
            title="Manual",
            original_language=en,
            author=author,
        )
        edition = Edition.objects.create(
            work=work,
            language=fr,
            seal=seal,
            title="Le Manuel",
            author="Epictete",
            adapter="Adapter",
            publisher="RinoBooks",
            language_code="en",
            frontispiece_template="{title}\n{language_display}",
            copyright_template="Droits\n{language_display}",
            about_edition_template="Edition francaise.",
        )
        BookEditionTemplate.objects.create(
            book_code=work.code,
            language="fr",
            title="Le Manuel",
            author_name="Epictete",
            publication_year=2026,
            frontispiece_text="{title}\n{language}",
            copyright_text="Droits\n{language}",
            about_edition_text="Edition francaise.",
        )

        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            build_frontmatter_files(edition, base)

            self.assertTrue((base / work.code / "fr" / "frontispiece.md").exists())
            self.assertFalse((base / work.code / "en" / "frontispiece.md").exists())
