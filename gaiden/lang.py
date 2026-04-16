from __future__ import annotations

import re


_ALIASES = {
    "": "",
    "english": "en",
    "eng": "en",
    "en-us": "en",
    "en_us": "en",
    "portuguese": "ptbr",
    "pt-br": "ptbr",
    "pt_br": "ptbr",
    "brazilian_portuguese": "ptbr",
    "br": "ptbr",
    "spanish": "es",
    "german": "de",
    "french": "fr",
    "italian": "it",
}


def normalize_lang_code(value: str | None, default: str = "en") -> str:
    raw = (value or "").strip().lower()
    if not raw:
        raw = default
    raw = raw.replace(" ", "_")
    raw = re.sub(r"[^a-z0-9_-]+", "_", raw).strip("_")
    return _ALIASES.get(raw, raw.replace("-", "_"))


def normalize_source_lang(value: str | None, default: str = "en") -> str:
    return normalize_lang_code(value, default=default)
