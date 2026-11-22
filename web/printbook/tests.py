import hashlib
import json
import os
import subprocess
import shutil
import unittest
from decimal import Decimal
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

from django.test import RequestFactory, TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from editorial.models import Contributor, Edition, EditionBuild, Language, Seal, Work
from gaiden.print_engine.geometry import CoverGeometry
from web.printbook.models import CoverBuild, InteriorBuild, PreflightReport, PrintEdition, PrintExport, TypographyProfile
from web.printbook.services import (
    approve_interior,
    approve_automatic_montage,
    design_cover,
    import_automatic_cover_pdf,
    export_package,
    fail,
    guard,
    invalidate,
    official_composition_preset,
    preflight,
    render_preview,
    root_for,
    save_composition,
    sha,
    snapshot,
    validate_pdf_page_size,
    workflow_steps,
    cover_spec_for,
)
from web.printbook.views import artifact, preflight_workspace, preview_panels


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

    def _record(self, name, content=b"artifact"):
        path = self.storage_root / name
        path.write_bytes(content)
        return {"path": str(path), "sha256": sha(path)}

    def test_interior_approval_records_exact_build_and_configuration(self):
        # Views load the edition and its decimal typography fields from the
        # database; mirror that path instead of keeping model default integers
        # cached on the freshly-created related object.
        self.edition.refresh_from_db()
        interior = self._interior()
        interior.files = {"pdf": self._record("INTERIOR.pdf", b"%PDF-approved")}
        interior.save(update_fields=["files"])

        with patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.storage_root)}), patch.object(
            EditionBuild, "qualifies_as_done", new_callable=PropertyMock, return_value=True
        ):
            approve_interior(self.edition)

        self.edition.refresh_from_db()
        self.assertEqual(self.edition.interior_approval["build_id"], interior.id)
        self.assertEqual(self.edition.interior_approval["sha256"], interior.files["pdf"]["sha256"])
        self.assertEqual(self.edition.interior_approval["configuration"], snapshot(self.edition))
        self.assertEqual(workflow_steps(self.edition)[1]["status_label"], "Aprovado")

    def test_configured_cover_pdf_is_ready_and_skips_manual_cover_stages(self):
        self.edition.refresh_from_db()
        interior = self._interior()
        spec = cover_spec_for(self.edition, interior.page_count, Decimal("0.0045"))
        geometry = CoverGeometry.from_spec(spec)
        upload = SimpleUploadedFile(
            "configured-cover.pdf",
            self._proof_pdf(geometry.spread_width_in, geometry.spread_height_in, 1),
            content_type="application/pdf",
        )

        with patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.storage_root)}), patch.object(
            EditionBuild, "qualifies_as_done", new_callable=PropertyMock, return_value=True
        ):
            cover = import_automatic_cover_pdf(self.edition, upload)

        self.edition.refresh_from_db()
        self.assertEqual(cover.status, "READY")
        self.assertIn("configured_cover", cover.files)
        self.assertEqual(self.edition.cover_mode, "automatic_pdf")
        self.assertEqual(self.edition.cover_approval["build_id"], cover.id)
        self.assertEqual([step["label"] for step in workflow_steps(self.edition)], [
            "Configuração", "Miolo", "Capa automática", "Preflight", "Exportação",
        ])

    @staticmethod
    def _proof_pdf(width, height, pages):
        from PIL import Image

        stream = BytesIO()
        with Image.new("RGB", (round(width * 72), round(height * 72)), "white") as page:
            page.save(stream, "PDF", save_all=True, append_images=[page] * (pages - 1), resolution=72)
        return stream.getvalue()

    def _stapled_interior(self):
        self.edition.platform = "uiclap"
        self.edition.trim = "6.299213x9.055118"
        self.edition.save(update_fields=["platform", "trim"])
        self.edition.refresh_from_db()
        interior = self._interior()
        interior.page_count = 30
        geometry = CoverGeometry.from_spec(cover_spec_for(self.edition, 30, Decimal("0")))
        path = root_for(interior) / "INTERIOR.pdf"
        path.parent.mkdir(parents=True)
        path.write_bytes(self._proof_pdf(geometry.trim_width_in, geometry.trim_height_in, 30))
        interior.files = {"pdf": {"path": str(path), "sha256": sha(path)}}
        interior.save(update_fields=["page_count", "files"])
        approve_interior(self.edition)
        return geometry

    def test_stapled_configured_pdf_survives_guard_and_reaches_preflight(self):
        with patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.storage_root)}), patch.object(
            EditionBuild, "qualifies_as_done", new_callable=PropertyMock, return_value=True
        ):
            geometry = self._stapled_interior()
            upload = SimpleUploadedFile(
                "configured-cover.pdf",
                self._proof_pdf(geometry.spread_width_in, geometry.spread_height_in, 1),
                content_type="application/pdf",
            )
            cover = import_automatic_cover_pdf(self.edition, upload)
            self.assertEqual(cover.snapshot, snapshot(self.edition))
            # Existing uploads used a marker in place of the print snapshot.
            cover.snapshot = {"source": "automatic_pdf", "configured_pdf": True, "skips_back_and_spine": True}
            cover.save(update_fields=["snapshot"])
            self.edition.refresh_from_db()
            report = approve_automatic_montage(self.edition)
            page = preflight_workspace(RequestFactory().get("/printbook/preflight/"), self.edition.pk)

        self.edition.refresh_from_db()
        self.assertEqual(report.cover_id, cover.id)
        self.assertEqual(report.status, "BLOCKED")
        self.assertEqual(self.edition.cover_settings["automatic_montage_approval"]["report_id"], report.id)
        self.assertFalse(self.edition.edition_approved_at)
        self.assertContains(page, "Montagem aprovada")
        self.assertContains(page, "Baixar relatório JSON")

    @unittest.skipUnless(
        all(shutil.which(tool) for tool in ("lualatex", "pdfinfo", "pdffonts", "pdftotext")),
        "Real print tools are needed to compile and inspect the montage",
    )
    def test_stapled_manual_montage_compiles_and_surfaces_preflight_blockers(self):
        with patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.storage_root)}), patch.object(
            EditionBuild, "qualifies_as_done", new_callable=PropertyMock, return_value=True
        ):
            self._stapled_interior()
            cover = design_cover(
                self.edition, layout="classic", title="Front Proof", subtitle="", author="Test Author",
                back_text="Back Proof", spine_text="Excluded Spine", background_color="#FFFFFF",
                text_color="#000000", accent_color="#000000", cover_font_family="TeX Gyre Pagella",
                front_title_font_family="TeX Gyre Pagella", front_author_font_family="TeX Gyre Pagella",
                save_mode="draft",
            )
            self.assertEqual(cover.status, "DRAFT")
            self.assertEqual(Decimal(cover.geometry["spine_width_in"]), 0)
            text = subprocess.check_output(["pdftotext", cover.files["pdf"]["path"], "-"], text=True)
            self.assertIn("Front Proof", text)
            self.assertIn("Back Proof", text)
            self.assertNotIn("Excluded Spine", text)

            report = approve_automatic_montage(self.edition)
            self.assertEqual(self.edition.cover_settings["automatic_montage_approval"]["report_id"], report.id)
            self.assertEqual(artifact(report), Path(report.files["report"]["path"]))

            cover.findings.append({
                "code": "cover.front_image_resolution", "severity": "BLOCKER",
                "message": "A imagem da capa precisa de maior resolução.",
            })
            cover.save(update_fields=["findings"])
            blocked = approve_automatic_montage(self.edition)
            page = preflight_workspace(RequestFactory().get("/printbook/preflight/"), self.edition.pk)

        self.assertEqual(blocked.cover_id, cover.id)
        self.assertContains(page, "A imagem da capa precisa de maior resolução.")
        self.assertNotContains(page, "Montagem aprovada")
        self.assertContains(page, "Baixar relatório JSON")

    def test_composition_change_invalidates_only_print_derivatives(self):
        interior = self._interior()
        cover = CoverBuild.objects.create(
            edition=self.edition,
            version=1,
            status="READY",
            snapshot=snapshot(self.edition),
            interior=interior,
        )
        source_id = self.edition.source_id
        source_hash = self.edition.source_sha256
        settings = {
            "body_font": "TeX Gyre Pagella",
            "font_size_pt": "11",
            "leading_pt": "14",
            "paragraph_indent_em": "0.8",
            "inner_margin_in": "0.75",
            "outer_margin_in": "0.50",
            "top_margin_in": "0.55",
            "bottom_margin_in": "0.65",
            "header_style": "chapter",
            "footer_style": "page",
            "page_numbering": True,
            "chapter_opening": "recto",
        }

        self.assertTrue(save_composition(self.edition, settings))

        interior.refresh_from_db()
        cover.refresh_from_db()
        self.edition.refresh_from_db()
        self.assertEqual(interior.status, "INVALIDATED")
        self.assertEqual(cover.status, "INVALIDATED")
        self.assertEqual(self.edition.source_id, source_id)
        self.assertEqual(self.edition.source_sha256, source_hash)
        self.assertEqual(self.edition.composition_settings["paragraph_indent_em"], "0.8")

    def test_first_official_preset_is_persisted_for_the_compiler(self):
        self.edition.platform = "amazon_kdp"
        self.edition.composition_settings = {}
        self.edition.save(update_fields=["platform", "composition_settings"])
        preset = official_composition_preset(self.edition)

        self.assertTrue(save_composition(self.edition, preset["values"]))

        self.edition.refresh_from_db()
        self.assertEqual(self.edition.composition_settings["inner_margin_in"], "0.875")

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

    def test_quality_control_records_draft_cover_findings_without_enabling_production(self):
        self.edition.refresh_from_db()
        interior = self._interior()
        cover = CoverBuild.objects.create(
            edition=self.edition,
            version=1,
            status="DRAFT",
            snapshot=snapshot(self.edition),
            interior=interior,
            geometry={"spine_width_in": "0.1"},
            findings=[{"code": "cover.template_pending", "severity": "WARNING", "message": "Prova sem template."}],
        )
        self.edition.interior_approval = {"build_id": interior.id}
        self.edition.save(update_fields=["interior_approval"])
        pdf_report = SimpleNamespace(checks=[
            SimpleNamespace(code="spec.provider_policy", status=SimpleNamespace(value="fail"), message="provider missing"),
            SimpleNamespace(code="interior.exists", status=SimpleNamespace(value="pass"), message=str(self.storage_root / "INTERIOR.pdf")),
            SimpleNamespace(code="cover.geometry", status=SimpleNamespace(value="pass"), message="Geometria conferida."),
        ])

        with patch.dict(os.environ, {"GAIDEN_STORAGE_ROOT": str(self.storage_root)}), patch.object(
            EditionBuild, "qualifies_as_done", new_callable=PropertyMock, return_value=True
        ), patch("web.printbook.services.preflight_pdfs", return_value=pdf_report), patch(
            "web.printbook.services.tool_version", return_value="test"
        ), patch("web.printbook.services.validate_industrial") as industrial:
            for build, filename in ((interior, "INTERIOR.pdf"), (cover, "COVER.pdf")):
                path = root_for(build) / filename
                path.parent.mkdir(parents=True)
                path.write_bytes(b"%PDF-fixture")
                build.files = {"pdf": {"path": str(path), "sha256": sha(path)}}
                build.save(update_fields=["files"])
            report = preflight(self.edition)
            self.assertEqual(artifact(report), Path(report.files["report"]["path"]))

        report.refresh_from_db()
        self.assertEqual(report.cover_id, cover.id)
        self.assertEqual(report.status, "BLOCKED")
        self.assertTrue(any(item["code"] == "cover.proof_only" for item in report.findings))
        self.assertEqual(next(item for item in report.findings if item["code"] == "cover.geometry")["severity"], "INFO")
        self.assertEqual(next(item for item in report.findings if item["code"] == "spec.provider_policy")["severity"], "WARNING")
        self.assertEqual(next(item for item in report.findings if item["code"] == "interior.exists")["message"], "PDF do miolo localizado.")
        payload = json.loads(Path(report.files["report"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(payload["mode"], "proof_quality_control")
        industrial.assert_not_called()
        self.edition.refresh_from_db()
        self.assertFalse(self.edition.edition_approved_at)

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
        self.edition.edition_approved_at = timezone.now()
        self.edition.save(update_fields=["state", "edition_approved_at"])

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
