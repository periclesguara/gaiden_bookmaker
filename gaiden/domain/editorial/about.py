from __future__ import annotations

from typing import List, Optional

TEXT = {
    "en": {
        "about_contributor_title": "About the {roles}",
        "about_edition_title": "About This Book",
        "role_labels": {
            "adapter": "Adapter",
            "editor": "Editor",
            "translator": "Translator",
            "curator": "Curator",
            "reviewer": "Reviewer",
        },
        "and_word": "&",
        "comma": ", ",
        "about_contributor_default": (
            "{name} works on classical literature editions focused on clarity, structure, and reading flow.\n"
            "This imprint publishes curated public-domain works and licensed originals.\n"
        ),
        "about_edition_default": (
            "This edition was prepared for modern readers.\n"
            "Formatting, structure, and minor editorial adjustments may have been applied to improve readability.\n"
        ),
    },
    "es": {
        "about_contributor_title": "Sobre el/la {roles}",
        "about_edition_title": "Sobre esta edición",
        "role_labels": {
            "adapter": "Adaptador",
            "editor": "Editor",
            "translator": "Traductor",
            "curator": "Curador",
            "reviewer": "Revisor",
        },
        "and_word": "y",
        "comma": ", ",
        "about_contributor_default": (
            "{name} trabaja en ediciones de clásicos enfocadas en claridad, estructura y fluidez de lectura.\n"
            "Este sello publica obras de dominio público curadas y originales con licencia.\n"
        ),
        "about_edition_default": (
            "Esta edición fue preparada para lectores modernos.\n"
            "Se pueden haber aplicado ajustes editoriales menores para mejorar la legibilidad.\n"
        ),
    },
    "pt": {
        "about_contributor_title": "Sobre o/a {roles}",
        "about_edition_title": "Sobre esta edição",
        "role_labels": {
            "adapter": "Adaptador",
            "editor": "Editor",
            "translator": "Tradutor",
            "curator": "Curador",
            "reviewer": "Revisor",
        },
        "and_word": "e",
        "comma": ", ",
        "about_contributor_default": (
            "{name} trabalha em edições de clássicos com foco em clareza, estrutura e fluidez de leitura.\n"
            "Este selo publica obras em domínio público com curadoria e originais licenciados.\n"
        ),
        "about_edition_default": (
            "Esta edição foi preparada para leitores modernos.\n"
            "Formatação, estrutura e pequenos ajustes editoriais podem ter sido aplicados para melhorar a legibilidade.\n"
        ),
    },
    "it": {
        "about_contributor_title": "Note sul {roles}",
        "about_edition_title": "Su questa edizione",
        "role_labels": {
            "adapter": "Adattatore",
            "editor": "Editor",
            "translator": "Traduttore",
            "curator": "Curatore",
            "reviewer": "Revisore",
        },
        "and_word": "e",
        "comma": ", ",
        "about_contributor_default": (
            "{name} lavora a edizioni di classici con attenzione alla chiarezza, alla struttura e alla fluidità di lettura.\n"
            "Questo marchio pubblica opere di pubblico dominio curate e originali su licenza.\n"
        ),
        "about_edition_default": (
            "Questa edizione è stata preparata per i lettori moderni.\n"
            "Formattazione, struttura e piccoli interventi editoriali possono essere stati applicati per migliorare la leggibilità.\n"
        ),
    },
}


def _t(lang: str) -> dict:
    lang = (lang or "en").strip().lower()
    return TEXT.get(lang, TEXT["en"])


def _join_human(labels: List[str], translations: dict) -> str:
    labels = [x for x in labels if x]
    if not labels:
        return ""
    seen = set()
    out = []
    for label in labels:
        if label not in seen:
            seen.add(label)
            out.append(label)
    if len(out) == 1:
        return out[0]
    if len(out) == 2:
        return f"{out[0]} {translations['and_word']} {out[1]}"
    return f"{translations['comma'].join(out[:-1])} {translations['and_word']} {out[-1]}"


def _roles_label(role_codes: List[str], translations: dict) -> str:
    codes = [c.strip().lower() for c in (role_codes or []) if c and str(c).strip()]
    labels = [translations["role_labels"].get(code, code) for code in codes]
    roles = _join_human(labels, translations)
    return roles or "Contributor"


def about_contributor_block(
    contributor_name: str,
    contributor_role_codes: List[str],
    language_code: str = "en",
    custom_text: Optional[str] = None,
) -> str:
    translations = _t(language_code)
    roles = _roles_label(contributor_role_codes, translations)
    title = translations["about_contributor_title"].format(roles=roles)
    body = (custom_text or translations["about_contributor_default"]).format(name=contributor_name)
    return f"{title}\n\n{body}".strip() + "\n"


def about_edition_block(
    language_code: str = "en",
    custom_text: Optional[str] = None,
) -> str:
    translations = _t(language_code)
    title = translations["about_edition_title"]
    body = custom_text or translations["about_edition_default"]
    return f"{title}\n\n{body}".strip() + "\n"
