from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from gaiden.translate import run_translate_with_contract

from . import edition_meta, paths, utils


TRANSLATE_CONTRACTS = {
    "en": "gaiden/contracts/en_modern_2025.json",
    "es": "gaiden/contracts/en_es_2025.json",
    "ptbr": "gaiden/contracts/en_ptbr_2025.json",
    "de": "gaiden/contracts/en_de_krimi_2025.json",
    "fr": "gaiden/contracts/translate_fr_2026.json",
    "it": "gaiden/contracts/translate_it_2026.json",
}


@dataclass
class PipelineResult:
    translated_path: Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def run_translate_only(edition, target_language: str) -> PipelineResult:
    lang = utils.normalize_lang(target_language)
    if lang not in TRANSLATE_CONTRACTS:
        raise ValueError(f"No translate contract for language={lang}")

    contract_path = _project_root() / TRANSLATE_CONTRACTS[lang]
    run_translate_with_contract(contract_path)

    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    out_dir = Path(payload.get("out_dir", "")).expanduser()
    if not out_dir.is_absolute():
        out_dir = _project_root() / out_dir
    lang_dir = out_dir.name
    merged = out_dir / f"merge_translate_{lang_dir}.txt"
    return PipelineResult(translated_path=merged)
