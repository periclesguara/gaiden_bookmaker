from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.test import TestCase
from django.urls import reverse

from pipeline.models import IncrementalBlock, IncrementalEdition
from pipeline.services.incremental_export import export_changed_blocks
from pipeline.services.incremental_import import (
    canonical_manifest_sha256,
    import_manifest,
    load_manifest,
    preview_manifest,
    sha256_bytes,
)


class RecordingPublisher:
    def __init__(self):
        self.calls: list[str] = []
        self.data: dict[str, bytes] = {}

    def publish_bytes(self, relative_path: str, data: bytes) -> None:
        self.calls.append(relative_path)
        self.data[relative_path] = data


class FailingPublisher(RecordingPublisher):
    def publish_bytes(self, relative_path: str, data: bytes) -> None:
        super().publish_bytes(relative_path, data)
        if relative_path == "control/manifest.json":
            raise OSError("falha remota simulada")


class IncrementalImportTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gaiden-incremental-test-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def package(
        self,
        name: str,
        sequences: list[int],
        *,
        expected: int,
        versions: dict[int, int] | None = None,
        contents: dict[int, str] | None = None,
        statuses: dict[int, str] | None = None,
        edition_id: str = "book_9001:pt-BR:1",
    ) -> Path:
        versions = versions or {}
        contents = contents or {}
        statuses = statuses or {}
        package_dir = self.root / name
        blocks_dir = package_dir / "blocks"
        blocks_dir.mkdir(parents=True)
        blocks = []
        for sequence in sequences:
            content = contents.get(sequence, f"# Capítulo de teste\n\nConteúdo editorial {sequence}.\n")
            data = content.encode("utf-8")
            file_name = f"block_{sequence:04d}.md"
            (blocks_dir / file_name).write_bytes(data)
            blocks.append(
                {
                    "sequence": sequence,
                    "block_id": f"{edition_id}:p1:c1:b{sequence}",
                    "file_name": file_name,
                    "content_sha256": sha256_bytes(data),
                    "size_bytes": len(data),
                    "status": statuses.get(sequence, "READY"),
                    "version": versions.get(sequence, 1),
                    "source_block_id": None,
                }
            )
        payload = {
            "schema_version": 1,
            "job_id": f"{edition_id}-job",
            "work_id": edition_id.split(":", 1)[0],
            "edition_id": edition_id,
            "book_code": edition_id.split(":", 1)[0],
            "locale": "pt-BR",
            "status": "IN_PROGRESS",
            "expected_block_count": expected,
            "last_contiguous_sequence": 0,
            "next_sequence": 1,
            "blocks": blocks,
        }
        payload["manifest_sha256"] = canonical_manifest_sha256(payload)
        manifest = package_dir / "manifest.json"
        manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return manifest

    def test_import_then_reimport_is_noop_without_duplicates(self):
        manifest = self.package("first", list(range(1, 27)), expected=101)

        first = import_manifest(manifest, import_attempt=1)
        second = import_manifest(manifest, import_attempt=2)

        self.assertEqual(first["created"], list(range(1, 27)))
        self.assertEqual(first["next_sequence"], 27)
        self.assertEqual(second["noop"], list(range(1, 27)))
        self.assertEqual(IncrementalBlock.objects.count(), 26)

    def test_resume_cursor_stays_at_first_gap_then_advances(self):
        first = self.package("first", list(range(1, 27)), expected=35)
        gap_batch = self.package("gap", list(range(28, 36)), expected=35)
        missing = self.package("missing", [27], expected=35)

        import_manifest(first)
        gap_result = import_manifest(gap_batch)
        filled_result = import_manifest(missing)

        self.assertEqual(gap_result["last_contiguous_sequence"], 26)
        self.assertEqual(gap_result["next_sequence"], 27)
        self.assertEqual(gap_result["gaps"], [27])
        self.assertEqual(filled_result["last_contiguous_sequence"], 35)
        self.assertIsNone(filled_result["next_sequence"])

    def test_hash_change_without_version_increment_is_conflict(self):
        original = self.package("original", [20], expected=20)
        changed = self.package(
            "changed",
            [20],
            expected=20,
            contents={20: "# Alterado\n\nConteúdo diferente.\n"},
        )

        import_manifest(original)
        result = import_manifest(changed)

        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(result["conflicts"][0]["reason"], "HASH_CHANGED_WITHOUT_VERSION_INCREMENT")
        self.assertEqual(IncrementalBlock.objects.count(), 1)

    def test_higher_version_preserves_superseded_version(self):
        original = self.package("original", [1], expected=1)
        changed = self.package(
            "changed",
            [1],
            expected=1,
            versions={1: 2},
            contents={1: "# Versão dois\n\nConteúdo revisto.\n"},
        )

        import_manifest(original)
        result = import_manifest(changed)

        self.assertEqual(result["updated"], [1])
        versions = list(IncrementalBlock.objects.order_by("version"))
        self.assertEqual([block.version for block in versions], [1, 2])
        self.assertEqual(versions[0].status, "SUPERSEDED")
        self.assertFalse(versions[0].is_current)
        self.assertTrue(versions[1].is_current)

    def test_failure_at_sequence_40_keeps_1_through_39_confirmed(self):
        manifest = self.package("forty", list(range(1, 41)), expected=40)

        def fail_at_40(sequence: int) -> None:
            if sequence == 40:
                raise RuntimeError("falha parcial simulada")

        result = import_manifest(manifest, failure_injector=fail_at_40)

        self.assertEqual(result["created"], list(range(1, 40)))
        self.assertEqual(result["last_contiguous_sequence"], 39)
        self.assertEqual(result["next_sequence"], 40)
        self.assertEqual(IncrementalBlock.objects.count(), 39)

    def test_complete_requires_every_current_block_approved(self):
        ready = self.package("ready", [1, 2], expected=2)
        approved = self.package(
            "approved",
            [1, 2],
            expected=2,
            statuses={1: "APPROVED", 2: "APPROVED"},
        )

        ready_result = import_manifest(ready)
        approved_result = import_manifest(approved)

        self.assertEqual(ready_result["status"], "IN_PROGRESS")
        self.assertEqual(approved_result["noop"], [1, 2])
        self.assertEqual(approved_result["status"], "APPROVED")

    def test_preview_reports_create_noop_update_and_conflict(self):
        original = self.package("original", [1, 2, 3], expected=4)
        import_manifest(original)
        preview_package = self.package(
            "preview",
            [1, 2, 3, 4],
            expected=4,
            versions={2: 2},
            contents={2: "# Novo\n\nVersão superior.\n", 3: "# Conflito\n\nSem versão nova.\n"},
        )

        preview = preview_manifest(preview_package)

        self.assertEqual([row["action"] for row in preview.rows], ["NOOP", "UPDATE", "CONFLICT", "CREATE"])

    def test_export_publishes_changed_blocks_manifest_then_ack(self):
        manifest = self.package("export", [1, 2], expected=2)
        import_manifest(manifest)
        publisher = RecordingPublisher()

        first = export_changed_blocks(
            "book_9001:pt-BR:1",
            "ignored-in-test",
            publisher=publisher,
        )

        self.assertEqual(first["exported_sequences"], [1, 2])
        self.assertEqual(publisher.calls[:2], ["blocks/block_0001.md", "blocks/block_0002.md"])
        self.assertEqual(publisher.calls[-2:], ["control/manifest.json", "control/import-ack.json"])
        publisher.calls.clear()
        second = export_changed_blocks(
            "book_9001:pt-BR:1",
            "ignored-in-test",
            publisher=publisher,
        )
        self.assertEqual(second["exported_sequences"], [])
        self.assertFalse(any(path.startswith("blocks/") for path in publisher.calls))

    def test_failed_export_does_not_mark_blocks_as_exported(self):
        manifest = self.package("export-failure", [1], expected=1)
        import_manifest(manifest)

        with self.assertRaisesRegex(OSError, "falha remota simulada"):
            export_changed_blocks(
                "book_9001:pt-BR:1",
                "ignored-in-test",
                publisher=FailingPublisher(),
            )

        block = IncrementalBlock.objects.get()
        self.assertEqual(block.exported_sha256, "")
        self.assertIsNone(block.exported_at)

    def test_interface_previews_the_batch(self):
        manifest = self.package("web-preview", [1, 2], expected=3)

        response = self.client.post(
            reverse("pipeline_incremental_import"),
            {
                "manifest_path": str(manifest),
                "blocks_directory": "",
                "drive_destination": "",
                "import_attempt": 1,
                "stop_on_conflict": "on",
                "action": "preview",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "book_9001:pt-BR:1")
        self.assertContains(response, "Importar lote e salvar retomada")
        self.assertContains(response, '<span class="chip CREATE">CREATE</span>', count=2, html=True)


class Book0041ManifestFixtureTests(TestCase):
    def test_checkpoint_fixture_keeps_acceptance_cursor(self):
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "book_0041_pt_br_resume_manifest.json"

        payload = load_manifest(fixture_path)

        self.assertEqual(payload["edition_id"], "book_0041:pt-BR:1")
        self.assertEqual(payload["expected_block_count"], 101)
        self.assertEqual(payload["last_contiguous_sequence"], 26)
        self.assertEqual(payload["next_sequence"], 27)
        self.assertEqual(len(payload["blocks"]), 26)
