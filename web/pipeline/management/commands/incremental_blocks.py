from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from pipeline.services.incremental_export import export_changed_blocks
from pipeline.services.incremental_import import (
    ImportRunConflict,
    ManifestValidationError,
    import_manifest,
    preview_manifest,
    resume_state,
)


class Command(BaseCommand):
    help = "Preview, import, resume or export a manifest-driven incremental edition."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("preview", "import", "resume", "export"))
        parser.add_argument("--manifest")
        parser.add_argument("--blocks-dir")
        parser.add_argument("--edition-id")
        parser.add_argument("--destination")
        parser.add_argument("--attempt", type=int, default=1)
        parser.add_argument("--continue-on-conflict", action="store_true")
        parser.add_argument("--after-sequence", type=int)

    def handle(self, *args, **options):
        action = options["action"]
        try:
            if action == "preview":
                manifest = self._required(options, "manifest")
                preview = preview_manifest(manifest, blocks_directory=options.get("blocks_dir"))
                result = {
                    "edition_id": preview.manifest["edition_id"],
                    "locale": preview.manifest["locale"],
                    "expected_block_count": preview.manifest["expected_block_count"],
                    "found_count": preview.found_count,
                    "batch_start": preview.batch_start,
                    "batch_end": preview.batch_end,
                    "current_last_contiguous_sequence": preview.current_last_contiguous_sequence,
                    "current_next_sequence": preview.current_next_sequence,
                    "manifest_sha256": preview.manifest_sha256,
                    "rows": preview.rows,
                }
            elif action == "import":
                manifest = self._required(options, "manifest")
                result = import_manifest(
                    manifest,
                    blocks_directory=options.get("blocks_dir"),
                    stop_on_conflict=not options["continue_on_conflict"],
                    import_attempt=options["attempt"],
                )
            elif action == "resume":
                result = resume_state(self._required(options, "edition_id"))
            else:
                result = export_changed_blocks(
                    self._required(options, "edition_id"),
                    self._required(options, "destination"),
                    after_sequence=options.get("after_sequence"),
                )
        except (ManifestValidationError, ImportRunConflict, OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    @staticmethod
    def _required(options: dict, name: str) -> str:
        value = (options.get(name) or "").strip()
        if not value:
            raise CommandError(f"--{name.replace('_', '-')} é obrigatório para esta ação.")
        return value
