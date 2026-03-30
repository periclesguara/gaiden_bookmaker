def get_frontispiece_template_for_edition(edition):
    if edition.language_code == "pt-br":
        return "gaiden/frontispiece_pt_br.md.j2"
    if edition.language_code == "es":
        return "gaiden/frontispiece_es.md.j2"
    if edition.language_code == "de":
        return "gaiden/frontispiece_de.md.j2"
    if edition.language_code == "it":
        return "gaiden/frontispiece_it.md.j2"
    return "gaiden/frontispiece_en.md.j2"


def country_for_language(language_code: str, fallback: str) -> str:
    return {
        "en": "Brazil",
        "pt-br": "Brasil",
        "es": "Brasil",
        "de": "Brasilien",
        "it": "Brasile",
    }.get(language_code, fallback)


def get_section_template_for_language(section: str, language_code: str) -> str:
    suffix = {
        "pt-br": "pt_br",
        "en": "en",
        "es": "es",
        "de": "de",
        "it": "it",
    }.get(language_code, "en")
    return f"pipeline/{section}_{suffix}.md.j2"
