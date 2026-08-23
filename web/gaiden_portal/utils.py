def get_title_page_template_for_edition(edition):
    if edition.language_code == "pt-br":
        return "gaiden/title_page_pt_br.md.j2"
    if edition.language_code == "es":
        return "gaiden/title_page_es.md.j2"
    if edition.language_code == "de":
        return "gaiden/title_page_de.md.j2"
    return "gaiden/title_page_en.md.j2"


def get_frontispiece_template_for_edition(edition):
    """Legacy alias; new code should use the canonical Title Page name."""

    return get_title_page_template_for_edition(edition)


def country_for_language(language_code: str, fallback: str) -> str:
    return {
        "en": "Brazil",
        "pt-br": "Brasil",
        "es": "Brasil",
        "de": "Brasilien",
    }.get(language_code, fallback)


def get_section_template_for_language(section: str, language_code: str) -> str:
    suffix = {
        "pt-br": "pt_br",
        "en": "en",
        "es": "es",
        "de": "de",
    }.get(language_code, "en")
    return f"pipeline/{section}_{suffix}.md.j2"
