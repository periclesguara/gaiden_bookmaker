LANGUAGE_CHOICES = (
    ("en", "English"), ("pt-BR", "Português (Brasil)"), ("fr", "Français"),
    ("de", "Deutsch"), ("es", "Español"), ("it", "Italiano"),
)
LANGUAGE_NAMES = dict(LANGUAGE_CHOICES)


def infer_language(value):
    return {"English (United Kingdom)": "en", "English": "en", "en-GB": "en", "en-US": "en", "Português (Brasil)": "pt-BR", "ptbr": "pt-BR", "pt-br": "pt-BR"}.get(value, value)
