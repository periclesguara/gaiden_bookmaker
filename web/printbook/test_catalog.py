import os
from unittest.mock import patch
from zipfile import ZipFile

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from editorial.models import EditionBuild
from web.printbook.models import PrintEdition
from web.printbook.services import sha
from web.printbook import tests as build_tests


@override_settings(ALLOWED_HOSTS=["testserver"])
class DoneCatalogTests(TestCase):
    def setUp(self):
        build_tests.PrintBuildStateTests.setUp(self)
        PrintEdition.objects.all().delete()
        self.environment = patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.storage_root)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.epub = self.storage_root / "final.epub"
        with ZipFile(self.epub, "w") as archive:
            archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="EPUB/package.opf"/></rootfiles></container>')
            archive.writestr("EPUB/package.opf", '<package><manifest><item id="chapter" href="text/chapter.xhtml" media-type="application/xhtml+xml"/><item id="css" href="style.css" media-type="text/css"/></manifest><spine><itemref idref="chapter"/></spine></package>')
            archive.writestr("EPUB/text/chapter.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><head><link rel="stylesheet" href="../style.css"/></head><body><p>Approved EPUB text.</p></body></html>')
            archive.writestr("EPUB/style.css", 'p {color:black}')
            archive.writestr("private.txt", "Not a manifest resource")
        report = self.storage_root / "epubcheck.json"
        report.write_text('{}')
        now = timezone.now()
        for key, value in {
            "epub_path": str(self.epub), "artifact_sha256": sha(self.epub),
            "artifact_size_bytes": self.epub.stat().st_size, "artifact_source": "test",
            "official_body_path": str(self.source_file), "official_body_sha256": sha(self.source_file),
            "validated_at": now, "approved_at": now, "completed_at": now,
            "epubcheck_status": EditionBuild.EPUBCHECK_PASSED,
            "epubcheck_validated_sha256": sha(self.epub),
            "epubcheck_report_path": str(report), "epubcheck_report_sha256": sha(report),
        }.items():
            setattr(self.source, key, value)
        self.source.save()
        self.url = reverse("printbook:source", args=[self.source.pk])

    def test_done_catalog_opens_without_creating_print_edition(self):
        self.assertTrue(self.source.qualifies_as_done)
        response = self.client.get(reverse("printbook:home"))
        self.assertContains(response, self.url)
        response = self.client.get(self.url)
        self.assertContains(response, "Criar edição e abrir diagramação")
        self.assertEqual(PrintEdition.objects.count(), 0)

    def test_every_non_done_state_is_excluded_and_direct_access_blocked(self):
        for status, _label in EditionBuild.STATUS_CHOICES:
            if status == "DONE":
                continue
            with self.subTest(status=status):
                EditionBuild.objects.filter(pk=self.source.pk).update(status=status)
                self.assertNotContains(self.client.get(reverse("printbook:home")), self.url)
                self.assertEqual(self.client.get(self.url).status_code, 404)
                self.assertEqual(self.client.post(self.url, {}).status_code, 404)
                self.assertEqual(self.client.get(reverse("printbook:source_preview", args=[self.source.pk])).status_code, 404)
                self.assertEqual(self.client.get(reverse("printbook:source_asset", args=[self.source.pk, "EPUB/style.css"])).status_code, 404)
        self.assertEqual(PrintEdition.objects.count(), 0)

    def test_done_with_changed_epub_is_rejected(self):
        self.epub.write_bytes(b"changed")
        self.assertNotContains(self.client.get(reverse("printbook:home")), self.url)
        self.assertEqual(self.client.post(self.url, {}).status_code, 404)

    def test_create_freezes_source_and_redirects_to_print_dashboard(self):
        response = self.client.post(self.url, {"source": 999999, "name": "Paperback", "trim": "6x9", "paper": "cream", "typography": self.profile.pk, "isbn": "", "barcode_mode": "provider"})
        edition = PrintEdition.objects.get()
        self.assertRedirects(response, reverse("printbook:detail", args=[edition.pk]))
        self.assertEqual(edition.source_id, self.source.pk)
        self.assertEqual(edition.source_sha256, sha(self.source_file))

    def test_cover_workspace_is_separate_from_editorial_preview(self):
        response = self.client.post(self.url, {
            "source": self.source.pk,
            "name": "Paperback",
            "trim": "6x9",
            "paper": "cream",
            "typography": self.profile.pk,
            "isbn": "",
            "barcode_mode": "provider",
        })
        edition = PrintEdition.objects.get()
        response = self.client.get(reverse("printbook:cover", args=[edition.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "printbook/cover.html")
        self.assertContains(response, "Confecção da capa")
        self.assertContains(response, "Montar capa completa")

    def test_preview_uses_separate_template_and_unchanged_epub_members(self):
        before = sha(self.epub)
        response = self.client.get(reverse("printbook:source_preview", args=[self.source.pk]))
        self.assertTemplateUsed(response, "printbook/source_preview.html")
        self.assertNotContains(response, "Aprovar este preview")
        self.assertContains(response, 'sandbox=""')
        response = self.client.get(reverse("printbook:source_asset", args=[self.source.pk, "EPUB/text/chapter.xhtml"]))
        self.assertContains(response, "Approved EPUB text.")
        self.assertIn("sandbox", response["Content-Security-Policy"])
        self.assertEqual(sha(self.epub), before)
        self.assertEqual(self.client.get(reverse("printbook:source_asset", args=[self.source.pk, "private.txt"])).status_code, 404)

    def test_intake_done_row_has_printbook_action(self):
        from django.template.loader import render_to_string
        html = render_to_string("pipeline/production_dashboard.html", {"done_rows": [{"final_build": self.source, "edition": self.source.edition}]})
        self.assertIn(self.url, html)
        html = render_to_string("pipeline/production_dashboard.html", {"edited_rows": [{"edition": self.source.edition}]})
        self.assertNotIn(self.url, html)

    def test_intake_source_map_contains_only_eligible_done_builds(self):
        from web.printbook.catalog import latest_by_edition
        self.assertEqual(latest_by_edition([self.source.edition_id])[self.source.edition_id].pk, self.source.pk)
        EditionBuild.objects.filter(pk=self.source.pk).update(status="READY_FOR_APPROVAL")
        self.assertEqual(latest_by_edition([self.source.edition_id]), {})
