from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

SUPPORTED_LANGS = {"en", "pt", "es"}


@dataclass
class BookMeta:
    title: str
    author_original: str
    seal: str
    place: str
    year: int

    lang: str = "en"

    collaborator_name: Optional[str] = None
    collaborator_role: Optional[str] = None  # ex: "Adapter & Editor"
    parent_imprint: Optional[str] = None     # ex: "RinoBooks"

    about_work: Optional[str] = None
    about_contributor: Optional[str] = None


def _normalize_lang(lang: str) -> str:
    lang = (lang or "en").lower()
    return lang if lang in SUPPORTED_LANGS else "en"


LANG_STRINGS = {
    "en": {
        "by": "by",
        "copyright_word": "Copyright",
        "about_work_title": "About this work / edition",
        "about_contributor_title": "About the contributor",
        "contributor_label": "Contributor",
        "copyright_clause": (
            "No part of this book may be reproduced or transmitted in any form "
            "or by any means, electronic or mechanical, including photocopying, "
            "recording, or by any information storage and retrieval system, "
            "without permission in writing from the publisher."
        ),
        "public_domain_note": (
            "This edition is based on a public domain text. Editorial work, "
            "introduction, notes, and layout of this edition are © the publisher."
        ),
    },
    "pt": {
        "by": "por",
        "copyright_word": "Direitos autorais",
        "about_work_title": "Sobre esta obra / edição",
        "about_contributor_title": "Sobre o colaborador",
        "contributor_label": "Colaborador",
        "copyright_clause": (
            "Nenhuma parte deste livro pode ser reproduzida ou transmitida em qualquer "
            "forma ou por qualquer meio, eletrônico ou mecânico, incluindo fotocópia, "
            "gravação ou quaisquer sistemas de armazenamento e recuperação de informação, "
            "sem permissão por escrito do editor."
        ),
        "public_domain_note": (
            "Esta edição se baseia em um texto em domínio público. O trabalho editorial, "
            "introdução, notas e diagramação desta edição são © do editor."
        ),
    },
    "es": {
        "by": "por",
        "copyright_word": "Derechos de autor",
        "about_work_title": "Acerca de esta obra / edición",
        "about_contributor_title": "Acerca del colaborador",
        "contributor_label": "Colaborador",
        "copyright_clause": (
            "Ninguna parte de este libro puede reproducirse o transmitirse en ninguna forma "
            "ni por ningún medio, electrónico o mecánico, incluyendo fotocopiado, grabación "
            "o cualquier sistema de almacenamiento y recuperación de información, sin el "
            "permiso previo y por escrito del editor."
        ),
        "public_domain_note": (
            "Esta edición se basa en un texto de dominio público. El trabajo editorial, "
            "introducción, notas y maquetación de esta edición son © del editor."
        ),
    },
}


def title_page_text(meta: BookMeta) -> str:
    lang = _normalize_lang(meta.lang)
    ls = LANG_STRINGS[lang]

    lines: list[str] = []

    # TÍTULO + AUTOR (com "by/por")
    lines.append(meta.title.strip())
    lines.append(f"{ls['by']} {meta.author_original.strip()}")
    lines.append("")

    # COLABORADOR (se houver)
    if meta.collaborator_name:
        role = meta.collaborator_role or ls["contributor_label"]
        lines.append(f"{role}: {meta.collaborator_name.strip()}")
        lines.append("")

    # IMPRINT + LOCAL / ANO
    imprint_line = meta.seal.strip()
    if meta.parent_imprint:
        imprint_line += f" (a {meta.parent_imprint.strip()} imprint)"
    lines.append(imprint_line)
    lines.append(f"{meta.place.strip()}, {meta.year}")
    lines.append("")

    # BLOCO "ABOUT WORK"
    if meta.about_work:
        title = ls["about_work_title"]
        lines.append(title)
        lines.append("-" * len(title))
        lines.append(meta.about_work.strip())
        lines.append("")

    # BLOCO "ABOUT CONTRIBUTOR"
    if meta.about_contributor and meta.collaborator_name:
        title = ls["about_contributor_title"]
        lines.append(title)
        lines.append("-" * len(title))
        lines.append(meta.about_contributor.strip())
        lines.append("")

    return "\n".join(lines)


def frontispiece_text(meta: BookMeta) -> str:
    """Legacy alias for the canonical Title Page renderer."""

    return title_page_text(meta)


def copyright_page(meta: BookMeta) -> str:
    lang = _normalize_lang(meta.lang)
    ls = LANG_STRINGS[lang]

    lines: list[str] = []

    # PRIMEIRA LINHA: "Copyright / Direitos autorais / Derechos de autor"
    imprint_line = meta.seal.strip()
    if meta.parent_imprint:
        imprint_line += f" (a {meta.parent_imprint.strip()} imprint)"

    lines.append(f"{ls['copyright_word']} © {meta.year} {imprint_line}.")
    lines.append("")

    # TÍTULO + AUTOR
    lines.append(meta.title.strip())
    lines.append(f"{ls['by']} {meta.author_original.strip()}")
    lines.append("")

    # COLABORADOR
    if meta.collaborator_name:
        role = meta.collaborator_role or ls["contributor_label"]
        lines.append(f"{role}: {meta.collaborator_name.strip()}")
        lines.append("")

    # LOCAL
    lines.append(f"{meta.place.strip()}, {meta.year}")
    lines.append("")

    # CLÁUSULA COPYRIGHT + NOTA DOMÍNIO PÚBLICO
    lines.append(ls["copyright_clause"])
    lines.append("")
    lines.append(ls["public_domain_note"])
    lines.append("")

    return "\n".join(lines)
