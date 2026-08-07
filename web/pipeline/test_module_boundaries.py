import ast
import hashlib
import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import Client, TestCase
from django.core.management import call_command
from django.urls import reverse

from author_studio.models import Author, CanonicalText, Work as WriterWork, WorkSource
from editorial.models import (
    Contributor,
    Edition,
    EditionBuild,
    EditionBuildAuditEvent,
    Language,
    Seal,
    Work,
)
from gaiden.application.builds.final_epub_import import FinalEpubImportError, import_final_epub
from gaiden.application.builds.finalized_projects import sync_finalized_project
from pipeline.models import ProductionBookmark
from web.writer.models import Manuscript, WriterPromotionEvent
from web.writer.models import EditorialWorkLink
from web.writer.services import create_version, promote_version


class ModuleEntrypointTests(TestCase):
    def test_home_exposes_five_independent_entries(self):
        response = self.client.get(reverse("root"))
        self.assertEqual(response.status_code, 200)
        for label in ("Writer", "Intake", "Bookmaker — Manual / AI", "Collections", "Projetos Finalizados"):
            self.assertContains(response, label)

    def test_home_links_to_writer(self):
        response = self.client.get(reverse("root"))
        self.assertContains(response, reverse("writer:home"))
        self.assertContains(response, "Writer")

    def test_dashboards_link_to_home_and_writer(self):
        dashboard_urls = (reverse("production_dashboard"), "/pipeline/")
        for dashboard_url in dashboard_urls:
            with self.subTest(dashboard_url=dashboard_url):
                response = self.client.get(dashboard_url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, reverse("root"))
                self.assertContains(response, "Página inicial")
                self.assertContains(response, reverse("writer:home"))
                self.assertContains(response, "Writer")

    def test_module_routes_are_independent(self):
        for name in ("writer:home", "intake:home", "manual_ai:home"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_manual_ai_stages_remain_accessible_without_running_agents(self):
        for name in ("manual_ai:translate", "manual_ai:refine", "manual_ai:polish"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Select an edition")

    def test_intake_ui_has_no_ai_execution_buttons(self):
        response = self.client.get(reverse("intake:home"))
        for forbidden in ("AI Translate", "AI Refine", "AI Polish"):
            self.assertNotContains(response, forbidden)

    def test_dashboard_get_has_no_side_effect(self):
        before = ProductionBookmark.objects.count()
        response = self.client.get(reverse("production_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ProductionBookmark.objects.count(), before)

    def test_home_and_finalized_gets_have_no_side_effects(self):
        before = {
            "bookmarks": ProductionBookmark.objects.count(),
            "audits": EditionBuildAuditEvent.objects.count(),
            "builds": EditionBuild.objects.count(),
        }
        self.assertEqual(self.client.get(reverse("root")).status_code, 200)
        self.assertEqual(self.client.get(reverse("finalized_projects:list")).status_code, 200)
        self.assertEqual(
            {
                "bookmarks": ProductionBookmark.objects.count(),
                "audits": EditionBuildAuditEvent.objects.count(),
                "builds": EditionBuild.objects.count(),
            },
            before,
        )

    def test_collections_get_does_not_mutate_source_projects(self):
        from collections_module.models import Collection, CollectionItem

        before = (Collection.objects.count(), CollectionItem.objects.count(), EditionBuild.objects.count())
        self.assertEqual(self.client.get(reverse("collection_new")).status_code, 200)
        self.assertEqual(
            (Collection.objects.count(), CollectionItem.objects.count(), EditionBuild.objects.count()),
            before,
        )

    def test_critical_post_endpoints_enforce_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf_client.post(reverse("final_build_mark_outdated", args=[999])).status_code, 403)
        self.assertEqual(csrf_client.post(reverse("writer:promote", args=[999, 999])).status_code, 403)


class IntakeArchitectureTests(TestCase):
    FORBIDDEN = ("openai", "agent", "prompt", "translate", "refine", "polish")

    def test_intake_python_has_no_forbidden_imports(self):
        web_root = Path(__file__).resolve().parents[1]
        repo_root = web_root.parent
        roots = [
            web_root / "intake",
            web_root / "pipeline" / "views_incremental.py",
            repo_root / "gaiden" / "application" / "intake",
            repo_root / "gaiden" / "application" / "editorial_import",
        ]
        offenders = []
        paths = []
        for root in roots:
            paths.extend([root] if root.is_file() else root.rglob("*.py"))
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                if any(token in name.casefold() for name in names for token in self.FORBIDDEN):
                    offenders.append(str(path.relative_to(repo_root)))
        self.assertEqual(offenders, [])

    def test_writer_and_dashboard_do_not_import_intake_or_agent_execution(self):
        web_root = Path(__file__).resolve().parents[1]
        targets = list((web_root / "writer").rglob("*.py")) + [web_root / "gaiden_portal" / "views.py"]
        forbidden = ("intake", "openai", "agent", "prompt", "translate", "refine", "polish")
        offenders = []
        for path in targets:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                module = ""
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                elif isinstance(node, ast.Import):
                    module = " ".join(alias.name for alias in node.names)
                if module and any(token in module.casefold() for token in forbidden):
                    offenders.append(f"{path.name}:{module}")
        self.assertEqual(offenders, [])


class WriterWorkflowTests(TestCase):
    def setUp(self):
        self.media_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_temp.cleanup)
        media_override = self.settings(MEDIA_ROOT=self.media_temp.name)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.author = Author.objects.create(name="Writer", canonical_name="writer", slug="writer", code="AUT-000001")
        self.work = WriterWork.objects.create(
            author=self.author,
            title="Draft",
            canonical_title="draft",
            slug="draft",
            code="WRK-000001",
        )
        self.source = WorkSource.objects.create(
            work=self.work,
            code="SRC-000001",
            original_filename="draft.txt",
            extension=".txt",
            size_bytes=8,
            sha256=hashlib.sha256(b"original").hexdigest(),
        )
        self.source.stored_file.save("draft.txt", ContentFile(b"original"))
        self.canonical = CanonicalText.objects.create(
            work=self.work,
            source=self.source,
            code="TXT-000001",
            sha256=hashlib.sha256(b"original").hexdigest(),
            character_count=8,
            word_count=1,
        )
        self.canonical.text_file.save("canonical.txt", ContentFile(b"original"))
        self.manuscript = Manuscript.objects.create(work=self.work)
        language = Language.objects.create(code="en", name="English", native_name="English")
        contributor = Contributor.objects.create(name="Writer")
        editorial_work = Work.objects.create(
            code="book_writer_0001", title="Draft", original_language=language, author=contributor
        )
        EditorialWorkLink.objects.create(
            author_work=self.work,
            editorial_work=editorial_work,
            linked_by="test",
            reason="explicit test identity",
        )

    def test_version_does_not_promote_and_promotion_preserves_previous(self):
        version = create_version(self.manuscript, content="approved manuscript", change_note="reviewed")
        self.canonical.refresh_from_db()
        self.assertNotEqual(self.canonical.sha256, version.sha256)
        event = promote_version(version, editor_approval="Editor", reason="approved", actor="test")
        self.canonical.refresh_from_db()
        self.assertEqual(self.canonical.sha256, version.sha256)
        self.assertEqual(event.previous_canonical_sha256, hashlib.sha256(b"original").hexdigest())
        self.assertEqual(WriterPromotionEvent.objects.count(), 1)

    def test_promotion_endpoint_requires_post_and_confirmation(self):
        version = create_version(self.manuscript, content="new")
        url = reverse("writer:promote", args=[self.manuscript.id, version.id])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.post(url, {"editor_approval": "Editor"})
        self.canonical.refresh_from_db()
        self.assertNotEqual(self.canonical.sha256, version.sha256)

    def test_missing_explicit_identity_link_fails_closed(self):
        EditorialWorkLink.objects.all().delete()
        version = create_version(self.manuscript, content="blocked")
        old_sha = self.canonical.sha256
        with self.assertRaisesMessage(ValueError, "no explicit editorial identity link"):
            promote_version(version, editor_approval="Editor", reason="reviewed", actor="test")
        self.canonical.refresh_from_db()
        self.assertEqual(self.canonical.sha256, old_sha)
        self.assertEqual(WriterPromotionEvent.objects.count(), 0)


class FinalEpubImportTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.storage_root = self.root / "storage"
        self.storage_patch = patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.storage_root)})
        self.storage_patch.start()
        self.addCleanup(self.storage_patch.stop)
        self.source = self.root / "approved_v3.epub"
        self.body = self.storage_root / "official" / "official.txt"
        self.body.parent.mkdir(parents=True)
        self.body.write_text("official body", encoding="utf-8")
        with zipfile.ZipFile(self.source, "w") as archive:
            archive.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", '<?xml version="1.0"?><container><rootfiles><rootfile full-path="EPUB/book.opf"/></rootfiles></container>')
            archive.writestr("EPUB/book.opf", '<?xml version="1.0"?><package><manifest><item href="chapter.xhtml"/></manifest></package>')
            archive.writestr("EPUB/chapter.xhtml", '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>Text</body></html>')
        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        language = Language.objects.create(code="en", name="English", native_name="English")
        contributor = Contributor.objects.create(name="Author")
        work = Work.objects.create(code="book_0078", title="The Colour Out of Space", original_language=language, author=contributor)
        seal = Seal.objects.create(slug="wrecked-alien-machine", name="Wrecked Alien Machine")
        self.edition = Edition.objects.create(work=work, language=language, seal=seal, publisher="RinoBooks")

    def _import(self, **overrides):
        values = dict(
            edition_id=self.edition.id,
            locale="en-US",
            source_path=self.source,
            expected_sha256=self.sha,
            expected_size_bytes=self.source.stat().st_size,
            official_body_path=self.body,
            approved=True,
            skip_epubcheck_for_tests=True,
        )
        values.update(overrides)
        return import_final_epub(**values)

    def test_import_is_audited_done_and_idempotent(self):
        first = self._import()
        second = self._import()
        self.assertEqual(first.outcome, "IMPORTED")
        self.assertEqual(second.outcome, "NO_OP")
        self.assertTrue(first.build.qualifies_as_done)
        self.assertEqual(first.build.build_version, 3)
        self.assertEqual(EditionBuild.objects.count(), 1)
        self.assertEqual(EditionBuildAuditEvent.objects.count(), 1)
        audit = EditionBuildAuditEvent.objects.get(event_type="FINAL_ARTIFACT_IMPORTED")
        self.assertEqual(audit.details["validation"]["tool"], "EPUBCheck")
        self.assertIn("warnings", audit.details["validation"])
        self.assertEqual(Path(first.build.epub_path).read_bytes(), self.source.read_bytes())
        dashboard = self.client.get(reverse("production_dashboard"))
        self.assertContains(dashboard, "book_0078")
        self.assertContains(dashboard, "Download EPUB")
        self.assertNotContains(dashboard, "Continuar produção")
        download = self.client.get(reverse("final_build_download", args=[first.build.id]))
        self.assertEqual(hashlib.sha256(b"".join(download.streaming_content)).hexdigest(), self.sha)

    def test_finalized_projection_and_sync_are_idempotent(self):
        result = self._import()
        first = sync_finalized_project(self.edition.id, actor="test")
        second = sync_finalized_project(self.edition.id, actor="test")
        self.assertEqual(first.outcome, "PROJECTED")
        self.assertEqual(second.outcome, "NO_OP")
        self.assertEqual(
            EditionBuildAuditEvent.objects.filter(
                build=result.build, event_type="FINALIZED_PROJECT_SYNCED"
            ).count(),
            1,
        )
        response = self.client.get(reverse("finalized_projects:list"))
        self.assertContains(response, "book_0078")
        self.assertContains(response, "DONE · V3")
        self.assertContains(response, self.sha)

    def test_sync_management_command_does_not_duplicate_projection(self):
        self._import()
        call_command("sync_finalized_projects", book_code="book_0078", actor="test")
        call_command("sync_finalized_projects", book_code="book_0078", actor="test")
        self.assertEqual(
            EditionBuildAuditEvent.objects.filter(event_type="FINALIZED_PROJECT_SYNCED").count(),
            1,
        )

    def test_hash_locale_and_missing_edition_fail_closed(self):
        for overrides in (
            {"expected_sha256": "0" * 64},
            {"locale": "pt-BR"},
            {"edition_id": self.edition.id + 999},
        ):
            with self.assertRaises(FinalEpubImportError):
                self._import(**overrides)

    def test_previous_version_is_preserved_and_marked_outdated(self):
        previous = EditionBuild.objects.create(
            edition=self.edition,
            language_code="en",
            build_version=2,
            status=EditionBuild.STATUS_DONE,
            is_final=True,
        )
        result = self._import()
        previous.refresh_from_db()
        self.assertEqual(result.build.build_version, 3)
        self.assertEqual(previous.status, EditionBuild.STATUS_OUTDATED)
        self.assertFalse(previous.is_final)
        self.assertTrue(EditionBuild.objects.filter(pk=previous.pk).exists())

    def test_same_destination_name_with_different_bytes_is_refused(self):
        storage_root = self.root / "storage"
        destination = storage_root / "builds" / "book_0078" / "en-US" / self.source.name
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"different")
        with self.assertRaises(FinalEpubImportError):
            self._import()
        self.assertEqual(destination.read_bytes(), b"different")

    def test_missing_epubcheck_fails_and_preserves_failed_build(self):
        with patch("gaiden.application.builds.final_epub_import.shutil.which", return_value=None):
            with self.assertRaisesMessage(FinalEpubImportError, "not found"):
                self._import(skip_epubcheck_for_tests=False)
        failed = EditionBuild.objects.get()
        self.assertEqual(failed.status, EditionBuild.STATUS_FAILED)
        self.assertFalse(failed.validation_passed)
        self.assertEqual(failed.validation_report["tool"], "EPUBCheck")

    def test_same_bytes_different_locale_is_not_returned_as_no_op(self):
        first = self._import()
        second = self._import(locale="en-GB")
        self.assertEqual(first.outcome, "IMPORTED")
        self.assertEqual(second.outcome, "IMPORTED")
        self.assertEqual(second.build.locale, "en-GB")
        self.assertNotEqual(second.build.id, first.build.id)

    def test_database_failure_compensates_promoted_filesystem_artifact(self):
        destination = self.storage_root / "builds" / "book_0078" / "en-US" / self.source.name
        with patch("editorial.models.EditionPipeline.save", side_effect=RuntimeError("database failure")):
            with self.assertRaisesMessage(RuntimeError, "database failure"):
                self._import()
        self.assertFalse(destination.exists())
        failed = EditionBuild.objects.get()
        self.assertEqual(failed.status, EditionBuild.STATUS_FAILED)

    def test_outdated_post_removes_projection_and_preserves_artifact(self):
        result = self._import()
        path = Path(result.build.epub_path)
        response = self.client.post(
            reverse("final_build_mark_outdated", args=[result.build.id]),
            {"confirm": "yes", "reason": "new canonical body"},
        )
        self.assertEqual(response.status_code, 302)
        result.build.refresh_from_db()
        result.build.edition.pipeline.refresh_from_db()
        self.assertEqual(result.build.status, EditionBuild.STATUS_OUTDATED)
        self.assertFalse(result.build.is_final)
        self.assertNotEqual(result.build.edition.pipeline.current_stage, "DONE")
        self.assertTrue(path.exists())
        self.assertNotContains(self.client.get(reverse("finalized_projects:list")), "book_0078")

    def test_download_denies_corrupt_bytes_and_audits_failure(self):
        result = self._import()
        Path(result.build.epub_path).write_bytes(b"corrupt")
        response = self.client.get(reverse("final_build_download", args=[result.build.id]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(
            EditionBuildAuditEvent.objects.filter(
                build=result.build,
                event_type="FINAL_ARTIFACT_DOWNLOAD_INTEGRITY_FAILED",
            ).exists()
        )
