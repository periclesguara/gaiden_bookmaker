from __future__ import annotations

import re


CANONICAL_LANGUAGES = frozenset({"en", "en-us", "pt-br", "fr", "de", "es", "it"})
ALIASES = {
    "en_us": "en-us",
    "enus": "en-us",
    "ptbr": "pt-br",
    "pt_br": "pt-br",
}


def canonical_language(value: str, *, allow_source_en: bool = True) -> str:
    token = (value or "").strip().lower().replace("_", "-")
    token = ALIASES.get(token, token)
    if not token or not re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", token):
        raise ValueError("Invalid language identifier")
    allowed = CANONICAL_LANGUAGES if allow_source_en else CANONICAL_LANGUAGES - {"en"}
    if token not in allowed:
        raise ValueError(f"Unsupported language identifier: {token}")
    return token


def internal_language(value: str) -> str:
    """Convert a canonical external identifier to the legacy underscore token."""
    return canonical_language(value).replace("-", "_")
