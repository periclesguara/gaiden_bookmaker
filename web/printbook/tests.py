import hashlib
import json
import os
import subprocess
from decimal import Decimal
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import PropertyMock, patch

from django.test import TestCase

from editorial.models import Contributor, Edition, EditionBuild, Language, Seal, Work
from web.printbook.models import CoverBuild, InteriorBuild, PreflightReport, PrintEdition, PrintExport, TypographyProfile
from web.printbook.services import export_package, fail, guard, invalidate, render_preview, sha, snapshot, validate_pdf_page_size
from web.printbook.views import preview_panels


class PrintBuildStateTests(TestCase):
    def setUp(self):
        language = Language.objects.create(code="en-print", name="English", native_name="English")
        contributor = Contributor.objects.create(name="Test Author")
        seal = Seal.objects.create(slug="print-test", name="Print Test")
        work = Work.objects.create(
            code="print-test-book",
            title="Print Test Book",
            original_language=language,
            author=contributor,
        )
        editorial_edition = Edition.objects.create(work=work, language=language, seal=seal)
        self.source = EditionBuild.objects.create(
            edition=editorial_edition,
            language_code="en",
            build_version=1,
            status=EditionBuild.STATUS_DONE,
            is_final=True,
            validation_passed=True,
        )
        self.profile = TypographyProfile.objects.create(name="Test", license_reference="OFL-1.1")
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.storage_root = Path(self.temporary.name)
        self.source_file = self.storage_root / "BOOK.MD_FINAL"
        self.source_file.write_text("# Chapter One\n\nApproved text.", encoding="utf-8")
        source_sha = hashlib.sha256(self.source_file.read_bytes()).hexdigest()
        self.edition = PrintEdition.objects.create(
            source=self.source,
            source_path=str(self.source_file),
            source_sha256=source_sha,
            name="Paperback",
            typography=self.profile,
        )

    def _interior(self, *, version=1, status="READY"):
        return InteriorBuild.objects.create(
            edition=self.edition,
            version=version,
            status=status,
            snapshot=snapshot(self.edition),
            page_count=2,
        )

    def test_invalidation_preserves_history_and_invalidates_every_dependency(self):
        interior = self._interior()
        cover = CoverBuild.objects.create(
            edition=self.edition,
            version=1,
            status="READY",
            snapshot=snapshot(self.edition),
            interior=interior,
        )
        report = PreflightReport.objects.create(
            edition=self.edition,
            version=1,
            status="READY",
            snapshot=snapshot(self.edition),
            interior=interior,
            cover=cover,
        )
        export = PrintExport.objects.create(
            edition=self.edition,
            version=1,
            status="READY",
            snapshot=snapshot(self.edition),
            report=report,
        )

        invalidate(self.edition)

        for build in (interior, cover, report, export):
            build.refresh_from_db()
            self.assertEqual(build.status, "INVALIDATED")
            self.assertTrue(type(build).objects.filter(pk=build.pk).exists())
        self.edition.refresh_from_db()
        self.assertEqual(self.edition.state, PrintEdition.State.INVALIDATED)

    def test_failed_new_build_does_not_replace_previous_ready_build(self):
        previous = self._interior()
        failed = self._interior(version=2, status="RUNNING")

        fail(failed, ValueError("Entrada inválida."))

        previous.refresh_from_db()
        failed.refresh_from_db()
        self.assertEqual(previous.status, "READY")
        self.assertEqual(failed.status, "FAILED")
        self.assertIn("Entrada inválida", failed.findings[0]["message"])

    def test_export_remains_blocked_without_a_ready_preflight(self):
        self._interior()
        with patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.storage_root)}), patch.object(
            EditionBuild, "qualifies_as_done", new_callable=PropertyMock, return_value=True
        ):
            with self.assertRaisesMessage(ValueError, "Conclua o build anterior"):
                export_package(self.edition)

    def test_source_hash_drift_invalidates_derived_build(self):
        interior = self._interior()
        self.source_file.write_text("# Chapter One\n\nChanged after approval.", encoding="utf-8")

        with patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.storage_root)}), patch.object(
            EditionBuild, "qualifies_as_done", new_callable=PropertyMock, return_value=True
        ):
            with self.assertRaisesMessage(ValueError, "Fonte editorial alterada"):
                guard(self.edition)

        interior.refresh_from_db()
        self.edition.refresh_from_db()
        self.assertEqual(interior.status, "INVALIDATED")
        self.assertEqual(self.edition.state, PrintEdition.State.INVALIDATED)

    def test_preview_guides_mirror_interior_gutter_and_mark_cover_spine(self):
        interior = self._interior()
        interior_panels = preview_panels(interior, "interior", 1, 2)
        self.assertEqual(interior_panels[0]["safe"]["left"], "12.5000")
        self.assertEqual(interior_panels[1]["safe"]["right"], "12.5000")
        cover = CoverBuild(
            edition=self.edition,
            interior=interior,
            geometry={
                "spread_width_in": "12.85",
                "spread_height_in": "9.25",
                "trim_width_in": "6",
                "trim_height_in": "9",
                "spine_width_in": "0.6",
                "bleed_in": "0.125",
            },
        )
        cover_panel = preview_panels(cover, "cover", 1, 1)[0]
        self.assertLess(float(cover_panel["spine_left"]), float(cover_panel["spine_right"]))
        self.assertGreater(float(cover_panel["front_safe"]["width"]), 0)

    def test_export_preview_uses_poppler_and_requires_a_real_output(self):
        pdf = self.storage_root / "INTERIOR.pdf"
        pdf.write_bytes(b"%PDF-test")
        output = self.storage_root / "INTERIOR_PREVIEW.png"

        def fake_run(command, **_kwargs):
            Path(command[-1] + ".png").write_bytes(b"PNG")
            return subprocess.CompletedProcess(command, 0)

        with patch("web.printbook.services.shutil.which", return_value="/usr/bin/pdftoppm"), patch(
            "web.printbook.services.subprocess.run", side_effect=fake_run
        ):
            self.assertEqual(render_preview(pdf, output), output)
        self.assertEqual(output.read_bytes(), b"PNG")

    def test_successful_export_packages_current_artifacts_without_host_paths(self):
        artifacts = self.storage_root / "approved"
        artifacts.mkdir()
        interior_pdf = artifacts / "INTERIOR.pdf"
        interior_tex = artifacts / "INTERIOR.tex"
        cover_pdf = artifacts / "COVER.pdf"
        template_pdf = artifacts / "OFFICIAL_TEMPLATE.pdf"
        report_json = artifacts / "preflight.json"
        for path, content in (
            (interior_pdf, b"%PDF-interior"),
            (interior_tex, b"approved tex"),
            (cover_pdf, b"%PDF-cover"),
            (template_pdf, b"%PDF-template"),
            (report_json, b"{}"),
        ):
            path.write_bytes(content)
        file_record = lambda path: {"path": str(path), "sha256": sha(path)}
        interior = InteriorBuild.objects.create(
            edition=self.edition,
            version=1,
            status="READY",
            snapshot=snapshot(self.edition),
            page_count=2,
            files={"pdf": file_record(interior_pdf), "tex": file_record(interior_tex)},
        )
        cover = CoverBuild.objects.create(
            edition=self.edition,
            version=1,
            status="READY",
            snapshot=snapshot(self.edition),
            interior=interior,
            files={"pdf": file_record(cover_pdf), "template": file_record(template_pdf)},
        )
        PreflightReport.objects.create(
            edition=self.edition,
            version=1,
            status="READY",
            snapshot=snapshot(self.edition),
            interior=interior,
            cover=cover,
            files={"report": file_record(report_json)},
            findings=[{"code": "all", "severity": "INFO", "message": "verified"}],
        )
        self.edition.state = PrintEdition.State.PRINT_READY
        self.edition.save(update_fields=["state"])

        def fake_preview(_pdf, output):
            output.write_bytes(b"PNG")
            return output

        with patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.storage_root)}), patch.object(
            EditionBuild, "qualifies_as_done", new_callable=PropertyMock, return_value=True
        ), patch("web.printbook.services.render_preview", side_effect=fake_preview):
            exported = export_package(self.edition)

        package_path = Path(exported.files["package"]["path"])
        with zipfile.ZipFile(package_path) as package:
            self.assertIn("interior/INTERIOR.pdf", package.namelist())
            self.assertIn("cover/OFFICIAL_TEMPLATE.pdf", package.namelist())
            self.assertIn("preview/COVER_PREVIEW.png", package.namelist())
            metadata = json.loads(package.read("metadata.json"))
        self.assertNotIn(str(self.storage_root), json.dumps(metadata))
        self.assertEqual(metadata["build_versions"], {"interior": 1, "cover": 1, "preflight": 1})

    def test_cover_composition_versions_panels_and_requires_preflight(self):
        from decimal import Decimal
        from web.printbook.services import compose_cover
        self._interior()

        def fake_upload(_upload, path):
            path.write_bytes(b"%PDF-panel")

        def fake_compile(_command, **kwargs):
            (kwargs["cwd"] / "COVER.pdf").write_bytes(b"%PDF-cover")
            return subprocess.CompletedProcess(_command, 0)

        def fake_size(path):
            return (900, 666) if Path(path).name == "OFFICIAL_TEMPLATE.pdf" else (441, 666)

        with patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.storage_root)}), patch.object(
            EditionBuild, "qualifies_as_done", new_callable=PropertyMock, return_value=True
        ), patch("web.printbook.services.shutil.which", return_value="lualatex"), patch(
            "web.printbook.services.upload_pdf", side_effect=fake_upload
        ), patch("web.printbook.services.inspect_pdf_page_count", return_value=1), patch(
            "web.printbook.services.inspect_pdf_page_size", side_effect=fake_size
        ), patch("web.printbook.services.subprocess.run", side_effect=fake_compile), patch(
            "web.printbook.services.tool_version", return_value="test"
        ):
            cover = compose_cover(self.edition, object(), object(), object(), Decimal("0.25"))
        self.assertEqual(cover.status, "READY")
        self.assertEqual(set(cover.files), {"pdf", "tex", "front", "back", "template"})
        self.edition.refresh_from_db()
        self.assertEqual(self.edition.state, PrintEdition.State.COVER_READY)
        self.assertFalse(PreflightReport.objects.filter(edition=self.edition).exists())

    def test_cover_geometry_validation_rejects_wrong_page_box(self):
        with patch("web.printbook.services.inspect_pdf_page_size", return_value=(432, 648)):
            with self.assertRaisesMessage(ValueError, "deve medir 12.500 × 9.250 polegadas"):
                validate_pdf_page_size(
                    self.source_file,
                    width_in="12.500",
                    height_in="9.250",
                    label="O template oficial",
                )

    def test_text_cover_builder_versions_design_and_keeps_it_local(self):
        from web.printbook.services import design_cover
        self._interior()

        def fake_upload(_upload, path):
            path.write_bytes(b"%PDF-template")

        def fake_compile(_command, **kwargs):
            (kwargs["cwd"] / "COVER.pdf").write_bytes(b"%PDF-cover")
            return subprocess.CompletedProcess(_command, 0)

        with patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.storage_root)}), patch.object(
            EditionBuild, "qualifies_as_done", new_callable=PropertyMock, return_value=True
        ), patch("web.printbook.services.shutil.which", return_value="lualatex"), patch(
            "web.printbook.services.upload_pdf", side_effect=fake_upload
        ), patch("web.printbook.services.inspect_pdf_page_count", return_value=1), patch(
            "web.printbook.services.inspect_pdf_page_size", return_value=(900, 666)
        ), patch("web.printbook.services.subprocess.run", side_effect=fake_compile), patch(
            "web.printbook.services.tool_version", return_value="test"
        ):
            build = design_cover(
                self.edition,
                save_mode="final",
                template=object(),
                spine=Decimal("0.25"),
                layout="modern",
                title="A & B",
                subtitle="Edition 2",
                author="Author",
                back_text="Back copy",
                spine_text="A & B",
                background_color="#112233",
                text_color="#FFFFFF",
                accent_color="#D4A24C",
            )
        self.assertEqual(build.status, "READY")
        self.assertEqual(set(build.files), {"pdf", "tex", "template", "design"})
        design = json.loads(Path(build.files["design"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(design["layout"], "modern")
        self.assertEqual(design["title"], "A & B")
