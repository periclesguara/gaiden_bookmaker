from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from editorial.models import EditionMetadata


@dataclass(frozen=True)
class MetadataValidation:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return not self.errors


GENERIC_SEO_DESCRIPTIONS = {
    "book",
    "coming soon",
    "description",
    "generic description",
    "livro",
    "descrição",
    "descrição genérica",
    "em breve",
    "n/a",
    "placeholder",
    "todo",
}
GENERIC_SEO_MARKERS = {
    "coming soon",
    "descrição genérica",
    "generic description",
    "lorem ipsum",
    "placeholder",
    "texto genérico",
}


def _missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return not value
    return False


def _is_generic_seo_description(value: str) -> bool:
    normalized = " ".join((value or "").casefold().split()).strip(" .!?:;-")
    return normalized in GENERIC_SEO_DESCRIPTIONS or any(
        marker in normalized for marker in GENERIC_SEO_MARKERS
    )


def validate_metadata(metadata: EditionMetadata | None) -> MetadataValidation:
    if metadata is None:
        return MetadataValidation(
            errors=("Crie e valide o rascunho de Metadados e SEO antes de exportar.",),
            warnings=(),
        )

    errors: list[str] = []
    required = {
        "edition_code": "Código da edição",
        "commercial_title": "Título comercial",
        "regional_language": "Idioma regional",
        "original_language": "Idioma original",
        "imprint_name": "Selo editorial",
        "publication_year": "Ano de publicação",
        "edition_format": "Formato da edição",
        "slug": "Slug",
        "seo_title": "Título SEO",
        "seo_description": "Descrição SEO",
        "description": "Descrição comercial completa",
        "short_description": "Descrição curta",
        "keywords": "Palavras-chave",
        "primary_category": "Categoria principal",
        "subcategory": "Subcategoria",
        "theme": "Tema",
        "target_audience": "Público-alvo",
        "cover_alt": "Texto alternativo da capa",
        "work_type": "Tipo da obra",
        "legal_basis": "Base jurídica",
        "edition_nature": "Natureza da edição",
        "editorial_modifications": "Resumo das modificações editoriais",
        "authorized_territories": "Territórios autorizados",
        "rights_evidence": "Evidências ou observações de direitos",
        "price": "Preço",
        "currency": "Moeda",
    }
    for field_name, label in required.items():
        if _missing(getattr(metadata, field_name, None)):
            errors.append(f"{label} é obrigatório para exportação.")

    if not (metadata.author_first_name or metadata.author_last_name):
        errors.append("Informe ao menos o nome ou sobrenome do autor.")

    allowed_languages = {
        value for value, _label in EditionMetadata.RegionalLanguage.choices
    }
    if (
        metadata.regional_language
        and metadata.regional_language not in allowed_languages
    ):
        errors.append("Idioma regional não é suportado pelo contrato RinoBooks.")

    if metadata.work_type in {
        EditionMetadata.WorkType.PUBLIC_DOMAIN,
        EditionMetadata.WorkType.DERIVATIVE,
    }:
        if metadata.base_work_year is None:
            errors.append(
                "Ano da obra-base é obrigatório para esta modalidade de direitos."
            )
        if not metadata.consulted_source.strip():
            errors.append(
                "Fonte consultada é obrigatória para esta modalidade de direitos."
            )

    if metadata.seo_description and _is_generic_seo_description(
        metadata.seo_description
    ):
        errors.append("A descrição SEO não pode ser um texto genérico.")

    if metadata.price is not None and metadata.price < 0:
        errors.append("O preço não pode ser negativo.")

    warnings: list[str] = []
    seo_title_length = len(metadata.seo_title.strip())
    if metadata.seo_title and not 45 <= seo_title_length <= 60:
        warnings.append(
            f"Título SEO tem {seo_title_length} caracteres; recomenda-se entre 45 e 60."
        )
    seo_description_length = len(metadata.seo_description.strip())
    if metadata.seo_description and not 120 <= seo_description_length <= 160:
        warnings.append(
            "Descrição SEO tem "
            f"{seo_description_length} caracteres; recomenda-se entre 120 e 160."
        )

    return MetadataValidation(tuple(errors), tuple(warnings))


def rights_statement(metadata: EditionMetadata) -> str:
    parts = [
        metadata.get_work_type_display(),
        metadata.legal_basis.strip(),
        metadata.edition_nature.strip(),
        metadata.editorial_modifications.strip(),
    ]
    return " ".join(part for part in parts if part)


def price_cents(metadata: EditionMetadata) -> int | None:
    if metadata.price is None:
        return None
    cents = (metadata.price * Decimal("100")).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return int(cents)
