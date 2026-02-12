from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from gaiden.contracts_v2.resolver import resolve_translate_contract_path
from gaiden.lang import normalize_lang_code


def _normalize_lang(lang: str) -> str:
    raw = (lang or "").strip().lower()
    raw = raw.replace("_", "-")
    if raw in {"pt-br", "ptbr"}:
        return "ptbr"
    return raw


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

        try:
            contract_path = resolve_translate_contract_path(lang)
            contract = _load_json(contract_path)
        except Exception as exc:
            errors.append(f"contract resolve/load failed for lang={lang}: {exc}")
            contract = None

        if contract:
            if contract.get("stage") != "translate":
                errors.append("contract stage != translate")
            if contract.get("model") != "gpt-5.2":
                errors.append("contract model != gpt-5.2")
            if contract.get("model_lock") is not True:
                errors.append("contract model_lock != true")
            tgt = normalize_lang_code(contract.get("language_target", ""), default="en_modern")
            if tgt != normalize_lang_code(lang, default="en_modern"):
                errors.append(f"contract language_target mismatch (contract={tgt} arg={lang})")

        if errors:
            for err in errors:
                self.stdout.write(f"[FAIL] {err}")
            raise CommandError("translate dry-run failed")

        self.stdout.write("[OK] translate dry-run precheck")
