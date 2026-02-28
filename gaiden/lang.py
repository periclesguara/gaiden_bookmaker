from __future__ import annotations

LANG_ALIAS_MAP = {
    "en": "en",
    "en_us": "en",
    "en-us": "en",
    "en_gb": "en",
    "en-gb": "en",
    "en_2026": "en_modern",
    "en-modern": "en_modern",
    "en_modern": "en_modern",
    "enmodern": "en_modern",
    "ptbr": "ptbr",
    "pt-br": "ptbr",
    "pt_br": "ptbr",
    "es": "es",
    "de": "de",
    "fr": "fr",
    "it": "it",
}


def normalize_lang_code(lang: str | None, *, default: str = "en") -> str:
    raw = (lang or default).strip()
    if not raw:
        raw = default
    key = raw.lower().replace(" ", "")
    if key in LANG_ALIAS_MAP:
        return LANG_ALIAS_MAP[key]
    return key.replace("-", "").replace("_", "")


def normalize_source_lang(lang: str | None, *, default: str = "en") -> str:
    """
    Source language normalization for translation input (canonical: en).
    """
    code = normalize_lang_code(lang, default=default)
    if code == "en_modern":
        return "en"
    return code
