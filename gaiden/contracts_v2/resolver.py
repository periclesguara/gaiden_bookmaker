from __future__ import annotations

from pathlib import Path
from typing import Dict

from gaiden.lang import normalize_lang_code

CONTRACT_ROOT = Path(__file__).resolve().parent
TRANSLATE_LANG_DIR = CONTRACT_ROOT / "translate" / "lang"

TRANSLATE_CONTRACTS: Dict[str, str] = {
    "en_modern": "en_modern_2026.json",
    "ptbr": "ptbr_2026.json",
    "es": "es_2026.json",
    "fr": "fr_2026.json",
    "it": "it_2026.json",
    "de": "de_2026.json",
}


def resolve_translate_contract_path(target_lang: str) -> Path:
    canonical = normalize_lang_code(target_lang, default="en_modern")
    name = TRANSLATE_CONTRACTS.get(canonical)
    if not name:
        raise RuntimeError(f"No canonical translate contract for lang={target_lang} (canonical={canonical})")
    path = TRANSLATE_LANG_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Translate contract missing: {path}")
    return path
