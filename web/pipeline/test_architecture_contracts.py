from __future__ import annotations

import os
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from gaiden.application.pipeline.gates import preflight_gate
from gaiden.application.pipeline.ingest import extract_text_from_html
from gaiden.application.pipeline.normalization import normalize_text_v2
from gaiden.application.pipeline.translation import sanitize_generated_chunk_text
from gaiden.application.pipeline.status import (
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_PENDING,
    resolve_stage_status,
)
from gaiden.domain.editorial.about import about_edition_block
from gaiden.infrastructure import env, storage
from gaiden.interfaces import cli as gaiden_cli
from gaiden.interfaces import collections_cli as gaiden_collections_cli


class StorageContractsTests(SimpleTestCase):
    def test_storage_root_defaults_to_repo_data(self):
        self.assertEqual(storage.storage_root(), storage.repo_root() / "data")

    def test_frontmatter_dir_uses_canonical_storage_root(self):
        path = storage.frontmatter_dir("book_0001", "en")
        self.assertEqual(path, storage.storage_root() / "frontmatter" / "book_0001" / "en")

    def test_runtime_web_storage_is_flagged_in_diagnostic(self):
        diagnostic = storage.storage_diagnostic()
        self.assertEqual(diagnostic.canonical_root, storage.storage_root())
        self.assertTrue(diagnostic.deprecated_web_root.name == "data")


class EnvContractsTests(SimpleTestCase):
    def test_secret_loader_reads_repo_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            secrets_path = Path(temp_dir) / "gaiden.env"
            secrets_path.write_text("OPENAI_API_KEY=test-key\n", encoding="utf-8")
            old = os.environ.get("GAIDEN_SECRETS_FILE")
            os.environ["GAIDEN_SECRETS_FILE"] = str(secrets_path)
            try:
                env.load_repo_secrets(force_reload=True)
                self.assertEqual(env.get_openai_api_key(), "test-key")
            finally:
                if old is None:
                    os.environ.pop("GAIDEN_SECRETS_FILE", None)
                else:
                    os.environ["GAIDEN_SECRETS_FILE"] = old
                env.load_repo_secrets(force_reload=True)


class GateAndStatusTests(SimpleTestCase):
    def test_preflight_gate_blocks_when_merge_is_missing(self):
        result = preflight_gate(
            editorial_ready=True,
            merge_refine_clean_path=Path("/tmp/gaiden-missing-merge-refine-clean.txt"),
        )
        self.assertFalse(result.ok)

    def test_stage_status_requires_non_empty_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "artifact.txt"
            output.write_text("ok\n", encoding="utf-8")
            status = resolve_stage_status(required_outputs=[output])
            self.assertEqual(status.status, STATUS_COMPLETED)

    def test_stage_status_blocks_explicitly(self):
        status = resolve_stage_status(blocked_reason="waiting")
        self.assertEqual(status.status, STATUS_BLOCKED)

    def test_stage_status_is_pending_when_output_missing(self):
        status = resolve_stage_status(required_outputs=[Path("/tmp/gaiden-missing-output.txt")])
        self.assertEqual(status.status, STATUS_PENDING)


class CompatibilityWrapperTests(SimpleTestCase):
    def test_root_wrappers_match_official_modules(self):
        from gaiden import about, ingest, normalize, translate, openai_client

        self.assertIs(about.about_edition_block, about_edition_block)
        self.assertIs(normalize.normalize_text_v2, normalize_text_v2)
        self.assertIs(ingest.extract_text_from_html, extract_text_from_html)
        self.assertIs(translate.sanitize_generated_chunk_text, sanitize_generated_chunk_text)
        self.assertEqual(openai_client.get_client.__module__, "gaiden.infrastructure.openai_client")


class ArchitectureSentinelTests(SimpleTestCase):
    def test_governance_documents_exist(self):
        required = [
            "docs/decisions/system-governance-matrix.md",
            "docs/decisions/collection-entry-flow.md",
            "docs/decisions/collection-storage-root.md",
            "docs/decisions/collection-handoff-to-pipeline.md",
            "docs/decisions/root-module-compatibility-hardening.md",
            "docs/diagnostics/web_data_audit.md",
            "docs/diagnostics/collection-module-inventory.md",
            "docs/diagnostics/web-residual-directories-audit.md",
            "docs/runbooks/run_collection_module.md",
            "docs/runbooks/run_collection_merge.md",
        ]
        for rel in required:
            self.assertTrue((storage.repo_root() / rel).exists(), rel)

    def test_no_active_legacy_imports_in_runtime_code(self):
        roots = [storage.repo_root() / "gaiden", storage.repo_root() / "web", storage.repo_root() / "scripts"]
        matches: list[str] = []
        allowed = {
            "web/pipeline/test_architecture_contracts.py",
            "web/pipeline/services/legacy_merges.py",
        }
        for root in roots:
            for path in root.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if "from legacy" in text or "import legacy" in text:
                    rel = str(path.relative_to(storage.repo_root()))
                    if rel not in allowed:
                        matches.append(rel)
        self.assertEqual(matches, [])

    def test_root_modules_are_compatibility_wrappers(self):
        root_files = [
            "gaiden/about.py",
            "gaiden/ingest.py",
            "gaiden/normalize.py",
            "gaiden/translate.py",
            "gaiden/openai_client.py",
            "gaiden/secrets.py",
        ]
        for rel in root_files:
            text = (storage.repo_root() / rel).read_text(encoding="utf-8")
            self.assertIn("Compatibility wrapper", text)

    def test_cli_exposes_official_subcommands(self):
        parser = gaiden_cli.build_parser()
        help_text = parser.format_help()
        self.assertIn("diagnostics", help_text)
        self.assertIn("normalize", help_text)
        self.assertIn("ingest-extract", help_text)

    def test_collections_cli_exposes_official_subcommands(self):
        parser = gaiden_collections_cli.build_parser()
        help_text = parser.format_help()
        self.assertIn("create", help_text)
        self.assertIn("add-item", help_text)
        self.assertIn("prepare", help_text)
        self.assertIn("normalize", help_text)
        self.assertIn("merge", help_text)
        self.assertIn("handoff", help_text)

    def test_no_absolute_repo_hardcode_in_scripts(self):
        for path in (storage.repo_root() / "scripts").glob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn("/home/periclesguara/Projetos/gaiden_bookmaker", text)

    def test_collection_storage_is_isolated_from_book_raw_namespace(self):
        from gaiden.infrastructure import collections_storage

        upload_path = collections_storage.item_upload_path("collection_0001", "en", 1, "source.html")
        self.assertIn("/data/collections/", str(upload_path))
        self.assertNotIn("/data/raw/", str(upload_path))

    def test_collection_merge_logic_does_not_live_in_view(self):
        text = (storage.repo_root() / "web/collections_module/views.py").read_text(encoding="utf-8")
        self.assertNotIn("merge_collection_items(", text)
        self.assertNotIn("merged_source_path(", text)

    def test_collection_handoff_is_blocked_before_merged(self):
        text = (storage.repo_root() / "web/collections_module/services/workflow.py").read_text(encoding="utf-8")
        service_text = (storage.repo_root() / "gaiden/application/collections/service.py").read_text(encoding="utf-8")
        self.assertIn("COLLECTION_MERGED", service_text)
        self.assertIn("Pipeline handoff is allowed only after COLLECTION_MERGED", service_text)
        self.assertIn("READY_FOR_PIPELINE", service_text)
        self.assertIn("manifest.json", service_text)

    def test_collection_runtime_code_does_not_use_web_data(self):
        roots = [
            storage.repo_root() / "gaiden/application/collections",
            storage.repo_root() / "gaiden/infrastructure",
            storage.repo_root() / "web/collections_module",
        ]
        offenders: list[str] = []
        allowed = {
            "gaiden/infrastructure/storage.py",
            "web/collections_module/tests.py",
        }
        for root in roots:
            for path in root.rglob("*.py"):
                rel = str(path.relative_to(storage.repo_root()))
                if rel in allowed:
                    continue
                text = path.read_text(encoding="utf-8")
                if "web/data" in text:
                    offenders.append(rel)
        self.assertEqual(offenders, [])

    def test_collection_runtime_code_does_not_hardcode_book_raw_input_namespace(self):
        roots = [
            storage.repo_root() / "gaiden/application/collections",
            storage.repo_root() / "gaiden/infrastructure",
            storage.repo_root() / "web/collections_module",
        ]
        offenders: list[str] = []
        allowed = {
            "gaiden/infrastructure/storage.py",
            "web/pipeline/test_architecture_contracts.py",
            "web/collections_module/tests.py",
        }
        for root in roots:
            for path in root.rglob("*.py"):
                rel = str(path.relative_to(storage.repo_root()))
                if rel in allowed:
                    continue
                text = path.read_text(encoding="utf-8")
                if "/data/raw/" in text or "data/raw/" in text:
                    offenders.append(rel)
        self.assertEqual(offenders, [])

    def test_collection_runtime_code_does_not_bypass_collections_storage_namespace(self):
        roots = [
            storage.repo_root() / "gaiden/application/collections",
            storage.repo_root() / "gaiden/infrastructure",
            storage.repo_root() / "web/collections_module",
        ]
        offenders: list[str] = []
        allowed = {
            "gaiden/infrastructure/collections_storage.py",
            "web/pipeline/test_architecture_contracts.py",
            "web/collections_module/tests.py",
        }
        for root in roots:
            for path in root.rglob("*.py"):
                rel = str(path.relative_to(storage.repo_root()))
                if rel in allowed:
                    continue
                text = path.read_text(encoding="utf-8")
                if "data/collections" in text:
                    offenders.append(rel)
        self.assertEqual(offenders, [])

    def test_collection_views_do_not_call_runner_directly(self):
        text = (storage.repo_root() / "web/collections_module/views.py").read_text(encoding="utf-8")
        self.assertNotIn("collections_runner", text)
        self.assertNotIn("collections_storage", text)

    def test_collection_runtime_code_does_not_depend_on_root_legacy_wrappers(self):
        roots = [
            storage.repo_root() / "gaiden/application/collections",
            storage.repo_root() / "gaiden/infrastructure",
            storage.repo_root() / "web/collections_module",
        ]
        offenders: list[str] = []
        disallowed = (
            "gaiden.ingest",
            "gaiden.normalize",
            "gaiden.translate",
            "gaiden.openai_client",
            "gaiden.secrets",
        )
        for root in roots:
            for path in root.rglob("*.py"):
                rel = str(path.relative_to(storage.repo_root()))
                if rel == "web/collections_module/tests.py":
                    continue
                text = path.read_text(encoding="utf-8")
                if any(token in text for token in disallowed):
                    offenders.append(rel)
        self.assertEqual(offenders, [])
