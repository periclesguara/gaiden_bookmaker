from __future__ import annotations

LANG_MAP = {
    "pt-br": "ptbr",
    "pt_br": "ptbr",
    "ptbr": "ptbr",
    "en-us": "en",
    "en_us": "en",
    "en": "en",
    "en_philo": "en",
    "en-philo": "en",
    "enphilo": "en",
    "english-philosofer": "en",
    "english_philosofer": "en",
    "englishphilosofer": "en",
    "english-philosopher": "en",
    "english_philosopher": "en",
    "englishphilosopher": "en",
    "es-es": "es",
    "es_es": "es",
    "es": "es",
    "de-de": "de",
    "de_de": "de",
    "de": "de",
    "fr-fr": "fr",
    "fr_fr": "fr",
    "fr": "fr",
    "it-it": "it",
    "it_it": "it",
    "it": "it",
}


def normalize_lang(code: str | None) -> str:
    if not code:
        return "en"
    key = code.strip().lower()
    return LANG_MAP.get(key, key.replace("-", "").replace("_", ""))
