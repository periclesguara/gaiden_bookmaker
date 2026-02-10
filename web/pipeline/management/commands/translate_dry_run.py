from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


def _normalize_lang(lang: str) -> str:
    raw = (lang or "").strip().lower()
    raw = raw.replace("_", "-")
    if raw in {"pt-br", "ptbr"}:
        return "ptbr"
    return raw


def _contract_mapping() -> dict[str, Path]:
    return {
        "en": Path("gaiden/contracts/en_modern_2025.json"),
        "es": Path("gaiden/contracts/en_es_2025.json"),
        "ptbr": Path("gaiden/contracts/en_ptbr_2025.json"),
        "de": Path("gaiden/contracts/en_de_krimi_2025.json"),
        "fr": Path("gaiden/contracts/translate_fr_2026.json"),
        "it": Path("gaiden/contracts/translate_it_2026.json"),
    }


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class Command(BaseCommand):
    help = "Dry-run translate precheck (filesystem only, no DB)."

    def add_arguments(self, parser):
        parser.add_argument("--book", required=True, help="book code (book_0003)")
        parser.add_argument("--lang", required=True, help="target language (de, ptbr, es, ...)")

    def handle(self, *args, **options):
        book_code = options["book"].strip()
        lang = _normalize_lang(options["lang"])
        project_root = Path(__file__).resolve().parents[4]
        data_dir = project_root / "data"

        errors: list[str] = []

        normalized_path = data_dir / "normalized" / book_code / "en" / f"{book_code}_en_v2.txt"
        if not normalized_path.exists():
            errors.append(f"normalized missing: {normalized_path}")
        elif normalized_path.stat().st_size == 0:
            errors.append(f"normalized empty: {normalized_path}")

        chunk_dir = data_dir / "chunks" / book_code / "en"
        manifest_path = chunk_dir / "chunks_manifest.json"
        run_report_path = chunk_dir / "chunk_run_report.json"
        if not manifest_path.exists():
            errors.append(f"chunks manifest missing: {manifest_path}")
        if not run_report_path.exists():
            errors.append(f"chunk run report missing: {run_report_path}")

        mapping = _contract_mapping()
        contract_path = mapping.get(lang)
        if not contract_path:
            errors.append(f"no contract mapping for lang={lang}")
            contract = None
        else:
            contract_path = project_root / contract_path
            if not contract_path.exists():
                errors.append(f"contract missing: {contract_path}")
                contract = None
            else:
                contract = _load_json(contract_path)

        if contract:
            output = contract.get("output") if isinstance(contract.get("output"), dict) else {}
            if not output.get("language"):
                errors.append("contract output.language missing")

            chunk_dir_val = str(contract.get("chunk_dir", "")).strip()
            if not chunk_dir_val:
                errors.append("contract chunk_dir missing")
            else:
                resolved = chunk_dir_val
                if "{BOOK_ID}" in resolved or "<BOOK_ID>" in resolved:
                    resolved = resolved.replace("{BOOK_ID}", book_code).replace("<BOOK_ID>", book_code)
                if not Path(resolved).is_absolute():
                    resolved = str((project_root / resolved).resolve())
                canonical = str(chunk_dir.resolve())
                if resolved != canonical:
                    errors.append(f"contract chunk_dir not canonical: {resolved}")

        if errors:
            for err in errors:
                self.stdout.write(f"[FAIL] {err}")
            raise CommandError("translate dry-run failed")

        self.stdout.write("[OK] translate dry-run precheck")
