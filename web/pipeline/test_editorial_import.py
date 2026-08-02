from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from editorial.models import EditionBuild, EditionPipeline, EditionText, Language, PipelineStage
from gaiden.application.editorial_import.preview import preview_editorial_import
from gaiden.application.editorial_import.service import StaleEditorialPreview, confirm_editorial_import
from gaiden.application.editorial_import.validation import EditorialPackageValidationError, sha256_bytes
from pipeline.models import IncrementalBlock, IncrementalEdition
from pipeline.services.incremental_import import ManifestValidationError, canonical_manifest_sha256


class FailingPublisher:
    def publish_bytes(self, relative_path: str, data: bytes) -> None:
        if relative_path == "control/manifest.json":
            raise OSError("falha remota simulada")


class EditorialImportAcceptanceTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gaiden-editorial-package-test-")
        self.root = Path(self.temp.name)
        Language.objects.create(code="en", name="English", native_name="English")
        Language.objects.create(code="ptbr", name="Portuguese", native_name="Português")

    def tearDown(self):
        self.temp.cleanup()

    def build_package(
        self,
        name: str,
        sequences: list[int],
        *,
        expected: int,
        book_code: str = "book_9001",
        locale: str = "pt-BR",
        language: str = "ptbr",
        versions: dict[int, int] | None = None,
        contents: dict[int, str] | None = None,
        package_status: str = "TEXTS_READY_FOR_EDITORIAL_REVIEW",
    ) -> tuple[Path, Path, Path]:
        versions = versions or {}
        contents = contents or {}
        package_root = self.root / name
        package_root.mkdir()
        source_data = b"Fonte editorial reservada e verificada.\n"
        source_path = package_root / "source.txt"
        source_path.write_bytes(source_data)
        body_text = "\n\n".join(contents.get(sequence, f"# Bloco {sequence}\n\nTexto {sequence}.") for sequence in range(1, expected + 1)) + "\n"
        body_path = package_root / "body.md"
        body_path.write_text(body_text, encoding="utf-8")
        package_payload = {
            "schema_version": 1,
            "package_type": "gaiden.editorial_import",
            "book": {
                "book_code": book_code,
                "title": "Obra de teste genérica",
                "author_name": "Autora de Teste",
                "source_language": "en",
                "original_publication_date": "1900-01-01",
                "original_author_death_date": "1950-01-01",
                "work_kind": "PUBLIC_DOMAIN",
                "publisher": "Editora Teste",
            },
            "source": {
                "original_name": "source.txt",
                "file_type": "txt",
                "path": "source.txt",
                "size": len(source_data),
                "sha256": sha256_bytes(source_data),
                "intake_item_id": 77,
            },
            "editions": [
                {
                    "language": language,
                    "locale": locale,
                    "metadata": {
                        "title": "Edição de teste",
                        "subtitle": "",
                        "publication_year": 2026,
                        "imprint_name": "MantaQuest",
                        "seal_name": "MantaQuest",
                        "editorial_name": "Editora Teste",
                        "editor_name": "Editor Teste",
                        "translator_name": "Tradutora Teste",
                        "adapter_name": "",
                        "collaborator_name": "Tradutora Teste",
                        "collaborator_pseudonym": "",
                        "collaborator_roles": "translator",
                        "edition_copyright_holder": "Editora Teste",
                    },
                    "frontmatter": {
                        "frontispiece_text": "Edição de teste",
                        "copyright_text": "Copyright editorial de teste",
                        "about_edition_text": "Edição em revisão.",
                        "about_contributor_text": "Contribuição editorial.",
                        "has_preface": False,
                        "preface_text": "",
                        "has_introduction": False,
                        "introduction_text": "",
                        "has_epilogue": False,
                        "epilogue_text": "",
                    },
                    "body": {
                        "status": "DRAFT",
                        "stage": "editorial_review",
                        "format": "markdown",
                        "path": "body.md",
                        "size": len(body_text.encode("utf-8")),
                        "sha256": sha256_bytes(body_text.encode("utf-8")),
                    },
                    "validation": {"status": "PASS", "report_path": "", "report_sha256": ""},
                    "artifacts": {"cover_path": "", "images_dir": "", "epub_path": "", "pdf_path": ""},
                }
            ],
            "status": package_status,
            "incremental": {"expected_block_count": expected},
        }
        package_path = package_root / "import-package.json"
        package_path.write_text(json.dumps(package_payload, ensure_ascii=False), encoding="utf-8")

        edition_id = f"{book_code}:{locale}:1"
        blocks = []
        for sequence in sequences:
            content = contents.get(sequence, f"# Bloco {sequence}\n\nTexto {sequence}.")
            data = content.encode("utf-8")
            file_name = f"block_{sequence:04d}.md"
            (package_root / file_name).write_bytes(data)
            blocks.append(
                {
                    "sequence": sequence,
                    "block_id": f"{edition_id}:section:b{sequence}",
                    "file_name": file_name,
                    "content_sha256": sha256_bytes(data),
                    "size_bytes": len(data),
                    "status": "READY",
                    "version": versions.get(sequence, 1),
                    "source_block_id": None,
                }
            )
        manifest_payload = {
            "schema_version": 1,
            "job_id": f"{edition_id}-job",
            "work_id": book_code,
            "edition_id": edition_id,
            "book_code": book_code,
            "locale": locale,
            "status": "READY",
            "expected_block_count": expected,
            "last_contiguous_sequence": 0,
            "next_sequence": 1,
            "blocks": blocks,
        }
        manifest_payload["manifest_sha256"] = canonical_manifest_sha256(manifest_payload)
        manifest_path = package_root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False), encoding="utf-8")
        return package_path, manifest_path, package_root

    def preview(self, paths):
        package, manifest, root = paths
        return preview_editorial_import(package, manifest, artifact_root=root, blocks_directory=root)

    def confirm(self, paths, *, attempt=1, **kwargs):
        package, manifest, root = paths
        preview = self.preview(paths)
        return confirm_editorial_import(
            package,
            manifest,
            artifact_root=root,
            blocks_directory=root,
            expected_package_sha256=preview["package_sha256"],
            expected_manifest_sha256=preview["manifest_sha256"],
            import_attempt=attempt,
            **kwargs,
        )

    def test_preview_is_read_only_and_classifies_complete_import(self):
        plan = self.preview(self.build_package("preview", [1, 2, 3], expected=3))
        self.assertTrue(plan["can_confirm"])
        self.assertEqual(plan["block_counts"]["CREATE"], 3)
        self.assertEqual(IncrementalEdition.objects.count(), 0)
        self.assertEqual(EditionText.objects.count(), 0)

    def test_complete_import_projects_frontmatter_and_body_without_build(self):
        result = self.confirm(self.build_package("complete", [1, 2, 3], expected=3))
        self.assertEqual(result["blocks"]["last_contiguous_sequence"], 3)
        self.assertIsNone(result["blocks"]["next_sequence"])
        texts = EditionText.objects.get()
        self.assertIn("# Bloco 3", texts.normalized_text)
        pipeline = EditionPipeline.objects.get()
        self.assertEqual(pipeline.current_stage, PipelineStage.RAW)
        self.assertEqual(EditionBuild.objects.count(), 0)
        self.assertFalse(result["published"])
        self.assertFalse(result["build_executed"])

    def test_partial_import_and_resume_after_gap(self):
        first = self.build_package("partial-1", [1, 2], expected=4)
        first_result = self.confirm(first)
        self.assertEqual(first_result["blocks"]["next_sequence"], 3)
        self.assertEqual(EditionText.objects.count(), 0)

        later = self.build_package("partial-gap", [4], expected=4)
        gap_result = self.confirm(later, attempt=2)
        self.assertEqual(gap_result["blocks"]["next_sequence"], 3)

        missing = self.build_package("partial-missing", [3], expected=4)
        resumed = self.confirm(missing, attempt=3)
        self.assertEqual(resumed["blocks"]["last_contiguous_sequence"], 4)
        self.assertIsNone(resumed["blocks"]["next_sequence"])
        self.assertEqual(EditionText.objects.count(), 1)

    def test_reimport_is_idempotent(self):
        paths = self.build_package("idempotent", [1, 2], expected=2)
        self.confirm(paths, attempt=1)
        second = self.confirm(paths, attempt=2)
        self.assertEqual(second["blocks"]["noop"], [1, 2])
        self.assertEqual(IncrementalBlock.objects.count(), 2)

    def test_changed_package_after_preview_is_rejected_as_stale(self):
        package, manifest, root = self.build_package("stale", [1], expected=1)
        plan = preview_editorial_import(package, manifest, artifact_root=root, blocks_directory=root)
        payload = json.loads(package.read_text(encoding="utf-8"))
        payload["book"]["title"] = "Pacote alterado depois da prévia"
        package.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(StaleEditorialPreview):
            confirm_editorial_import(
                package,
                manifest,
                artifact_root=root,
                blocks_directory=root,
                expected_package_sha256=plan["package_sha256"],
                expected_manifest_sha256=plan["manifest_sha256"],
            )
        self.assertEqual(IncrementalEdition.objects.count(), 0)

    def test_legacy_empty_source_uploaded_at_is_normalized_to_null(self):
        package, manifest, root = self.build_package("legacy-empty-uploaded-at", [1], expected=1)
        payload = json.loads(package.read_text(encoding="utf-8"))
        canonical_edition = payload["editions"][0]
        metadata = canonical_edition["metadata"]
        frontmatter = canonical_edition["frontmatter"]
        legacy = {
            "schema_version": 1,
            "mode": "automated",
            "pilot": "generic",
            "book_code": payload["book"]["book_code"],
            "status": payload["status"],
            "source": {
                "book_code": payload["book"]["book_code"],
                "title": payload["book"]["title"],
                "author": payload["book"]["author_name"],
                "source_language": "en",
                "source_path": "source.txt",
                "source_sha256": payload["source"]["sha256"],
                "item_id": 77,
            },
            "editions": [{
                "book_code": payload["book"]["book_code"],
                "language": canonical_edition["language"],
                "language_variant": canonical_edition["locale"],
                "title": metadata["title"],
                "author_name": payload["book"]["author_name"],
                "publication_year": metadata["publication_year"],
                "work_kind": "PUBLIC_DOMAIN",
                "imprint_name": metadata["imprint_name"],
                "seal_name": metadata["seal_name"],
                "source_file_size": payload["source"]["size"],
                "source_file_sha256": payload["source"]["sha256"],
                "source_uploaded_at": "",
                **frontmatter,
                "text_output": {
                    "body_path": canonical_edition["body"]["path"],
                    "body_sha256": canonical_edition["body"]["sha256"],
                    "body_status": "QA_PASS",
                },
            }],
            "incremental_import": {"expected_block_count": 1},
            "editorial_policy": {},
            "rights_policy": {},
            "gaiden_blockers_resolved": {},
            "pending_stages": [],
        }
        package.write_text(json.dumps(legacy), encoding="utf-8")

        result = self.confirm((package, manifest, root))

        from pipeline.models import BookEditionTemplate
        self.assertIsNone(BookEditionTemplate.objects.get().source_uploaded_at)
        self.assertEqual(result["blocks"]["last_contiguous_sequence"], 1)

    def test_hash_conflict_blocks_confirmation(self):
        self.confirm(self.build_package("conflict-original", [1], expected=1))
        changed = self.build_package("conflict-changed", [1], expected=1, contents={1: "# Alterado\n"})
        plan = self.preview(changed)
        self.assertFalse(plan["can_confirm"])
        with self.assertRaises(EditorialPackageValidationError):
            self.confirm(changed, attempt=2)
        self.assertEqual(IncrementalBlock.objects.count(), 1)

    def test_higher_version_preserves_history(self):
        self.confirm(self.build_package("version-1", [1], expected=1))
        second = self.build_package("version-2", [1], expected=1, versions={1: 2}, contents={1: "# Versão 2\n"})
        result = self.confirm(second, attempt=2)
        self.assertEqual(result["blocks"]["updated"], [1])
        self.assertEqual(list(IncrementalBlock.objects.order_by("version").values_list("version", flat=True)), [1, 2])
        self.assertEqual(IncrementalBlock.objects.get(version=1).status, "SUPERSEDED")

    def test_composite_confirmation_rolls_back_entire_current_batch(self):
        paths = self.build_package("rollback", [1, 2, 3], expected=3)

        def fail(stage: str) -> None:
            if stage == "after_blocks":
                raise RuntimeError("falha transacional simulada")

        with self.assertRaisesRegex(RuntimeError, "falha transacional"):
            self.confirm(paths, failure_injector=fail)
        self.assertEqual(IncrementalBlock.objects.count(), 0)
        self.assertEqual(IncrementalEdition.objects.count(), 0)
        self.assertEqual(EditionText.objects.count(), 0)

    def test_invalid_manifest_and_invalid_sha_are_rejected(self):
        package, manifest, root = self.build_package("invalid", [1], expected=1)
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["schema_version"] = 99
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ManifestValidationError):
            preview_editorial_import(package, manifest, artifact_root=root, blocks_directory=root)

        package, manifest, root = self.build_package("bad-sha", [1], expected=1)
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["blocks"][0]["content_sha256"] = "0" * 64
        payload["manifest_sha256"] = canonical_manifest_sha256(payload)
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        plan = preview_editorial_import(package, manifest, artifact_root=root, blocks_directory=root)
        self.assertFalse(plan["can_confirm"])

    def test_path_traversal_and_symlink_are_rejected(self):
        package, manifest, root = self.build_package("traversal", [1], expected=1)
        payload = json.loads(package.read_text(encoding="utf-8"))
        payload["source"]["path"] = "../source.txt"
        package.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(EditorialPackageValidationError):
            preview_editorial_import(package, manifest, artifact_root=root, blocks_directory=root)

        package, manifest, root = self.build_package("symlink", [1], expected=1)
        (root / "source.txt").unlink()
        (root / "outside.txt").write_text("fora", encoding="utf-8")
        (root / "source.txt").symlink_to(root / "outside.txt")
        with self.assertRaises(EditorialPackageValidationError):
            preview_editorial_import(package, manifest, artifact_root=root, blocks_directory=root)

    def test_drive_failure_does_not_mark_export_complete(self):
        paths = self.build_package("drive-failure", [1], expected=1)
        result = self.confirm(paths, drive_destination="ignored", publisher=FailingPublisher())
        self.assertEqual(result["drive"]["status"], "FAILED")
        block = IncrementalBlock.objects.get()
        self.assertEqual(block.exported_sha256, "")
        self.assertIsNone(block.exported_at)

    def test_book_0041_fixture_recognizes_101_blocks_in_review_only(self):
        paths = self.build_package("acceptance-101", list(range(1, 102)), expected=101, book_code="book_0041")
        result = self.confirm(paths)
        self.assertEqual(IncrementalBlock.objects.filter(is_current=True).count(), 101)
        self.assertEqual(result["blocks"]["last_contiguous_sequence"], 101)
        self.assertIsNone(result["blocks"]["next_sequence"])
        self.assertEqual(IncrementalBlock.objects.values("block_id").distinct().count(), 101)
        self.assertEqual(result["editorial_status"], "TEXTS_READY_FOR_EDITORIAL_REVIEW")
        self.assertEqual(EditionBuild.objects.count(), 0)
        self.assertEqual(EditionPipeline.objects.get().current_stage, PipelineStage.RAW)
        self.assertFalse(result["published"])

    def test_automated_dashboard_get_is_read_only(self):
        response = self.client.get(reverse("automated_editorial_import"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Automated Intake")
        self.assertContains(response, "Opção A · Google Drive")
        self.assertContains(response, "Opção B · Arquivos deste computador")
        self.assertContains(response, "0 de 3 seleções obrigatórias concluídas")
        self.assertContains(response, 'data-file-role="package"')
        self.assertContains(response, 'data-file-role="manifest"')
        self.assertContains(response, 'data-file-role="artifacts"')
        self.assertEqual(IncrementalEdition.objects.count(), 0)

    def test_automated_upload_preview_exposes_confirmation_without_writes(self):
        package, manifest, root = self.build_package("web", [1], expected=1)
        uploads = [
            SimpleUploadedFile(path.name, path.read_bytes())
            for path in (root / "source.txt", root / "body.md", root / "block_0001.md")
        ]
        response = self.client.post(
            reverse("automated_editorial_import_preview"),
            {
                "package_file": SimpleUploadedFile("package.json", package.read_bytes(), content_type="application/json"),
                "manifest_file": SimpleUploadedFile("manifest.json", manifest.read_bytes(), content_type="application/json"),
                "artifact_files": uploads,
                "drive_destination": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirmar importação")
        self.assertEqual(IncrementalEdition.objects.count(), 0)
