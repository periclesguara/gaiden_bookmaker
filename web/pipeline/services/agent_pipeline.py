from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path

from gaiden.contracts_v2.resolver import resolve_translate_contract_path
from gaiden.run_artifacts import create_run_dir, write_contract_json, write_env_json
from gaiden.secrets_loader import require_openai_ready
from gaiden.translate_engine_v1 import run_translate_safe

from . import edition_meta, paths, utils


@dataclass
class PipelineResult:
    translated_path: Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def run_translate_only(edition, target_language: str) -> PipelineResult:
    lang = utils.normalize_lang(target_language)
    contract_path = resolve_translate_contract_path(lang)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))

    book = edition_meta.book_code(edition)
    project_root = _project_root()
    chunks_root = project_root / "data" / "chunks"
    translated_root = project_root / "data" / "translated"
    runs_root = translated_root / "_runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    require_openai_ready(dry_run=False, repo_root=project_root)

    run_dir, run_id = create_run_dir(runs_root, f"translate_{book}_{lang}")
    write_contract_json(run_dir, contract)
    write_env_json(
        run_dir,
        dry_run=False,
        model=contract.get("model"),
        base_url=os.environ.get("OPENAI_BASE_URL"),
        repo_root=project_root,
        extra={"mode": "single", "book": book, "target_lang": lang},
    )
    contracts_root = project_root / "data" / "contracts_runtime"
    contracts_root.mkdir(parents=True, exist_ok=True)
    contracts_root.joinpath(f"translate_single_{run_id}.json").write_text(
        json.dumps(contract, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    result = run_translate_safe(
        book_id=book,
        chunk_dir=str(chunks_root / book / "en"),
        out_dir=str(translated_root / book / lang),
        suffix=lang,
        contract_path=contract_path,
        dry_run=False,
    )
    merged_path = result.get("merged_txt")
    if not merged_path:
        merged_path = str(translated_root / book / lang / "merge_refine_clean.txt")
    run_dir.joinpath("merged_v1.txt").write_text(Path(merged_path).read_text(encoding="utf-8"), encoding="utf-8")
    return PipelineResult(translated_path=Path(merged_path))
