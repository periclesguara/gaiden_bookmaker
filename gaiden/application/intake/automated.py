from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass


AUTOMATED_SCHEMA_VERSION = 1
BOOK_CODE_PATTERN = re.compile(r"^book_(\\d{4,})$")
ELIGIBLE_SOURCE_STATES = frozenset(
    {
        "DOWNLOADED",
        "CLEAN_READY",
        "READY_FOR_EDITING",
    }
)


@dataclass(frozen=True)
class EditionProfile:
    locale: str
    language: str
    pipeline_language: str
    label: str
    end_marker: str
    source_action: str


EDITION_PROFILES = (
    EditionProfile(
        "en-gb",
        "en",
        "en",
        "English (United Kingdom)",
        "THE END",
        "localize_en_gb",
    ),
    EditionProfile(
        "pt-br",
        "pt-br",
        "ptbr",
        "Português (Brasil)",
        "FIM",
        "translate",
    ),
)

COMMON_STAGES = (
    "body",
    "frontmatter",
    "introduction",
    "image_bank",
    "cover",
    "end_marker",
    "build",
    "epub_validation",
)


def _value(obj, name: str, default=""):
    value = getattr(obj, name, default)
    return value if value is not None else default


def _plan_sha256(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_automated_editorial_plan(item) -> dict:
    batch = item.batch
    title = str(_value(item, "confirmed_title") or _value(item, "suggested_title")).strip()
    author = str(_value(batch, "author_default")).strip()
    book_code = str(_value(item, "book_code")).strip()
    source_language = str(_value(batch, "source_language")).strip().lower()
    source_status = str(_value(item, "status")).upper()
    errors: list[str] = []
    warnings: list[str] = []

    if not BOOK_CODE_PATTERN.fullmatch(book_code):
        errors.append("O item precisa de um book_code canônico antes da automação.")
    if not title:
        errors.append("Confirme o título editorial.")
    if not author:
        errors.append("Informe o autor padrão do lote.")
    if not _value(item, "original_year", 0):
        errors.append("Informe o ano original da obra.")
    if _value(item, "duplicate_of_id", None):
        errors.append("Uma duplicata não pode iniciar o modo Automated.")
    if source_status not in ELIGIBLE_SOURCE_STATES:
        errors.append("O original precisa estar baixado e íntegro antes da automação.")
    if not _value(item, "original_path"):
        errors.append("O caminho canônico do original não está registrado.")
    if not _value(item, "source_sha256"):
        errors.append("O SHA-256 do original não está registrado.")
    if source_status in {"CLEAN_READY", "READY_FOR_EDITING"} and not _value(item, "clean_path"):
        errors.append("O clean.txt precisa estar registrado para o estado atual do item.")
    if source_language not in {"en", "en-gb", "en-uk"}:
        errors.append("Este piloto exige uma fonte em inglês.")
    if not bool(_value(batch, "public_domain", False)):
        errors.append("Confirme o domínio público do lote antes de gerar edições.")
    else:
        warnings.append(
            "Domínio público deve ser confirmado por território de distribuição; "
            "o plano não produz uma conclusão jurídica."
        )

    preparation_stages = []
    if source_status == "DOWNLOADED":
        preparation_stages.append(
            {
                "name": "clean_source",
                "status": "planned",
                "requires_approval": False,
            }
        )

    editions = []
    for profile in EDITION_PROFILES:
        stages = [
            {
                "name": profile.source_action,
                "status": "planned",
                "requires_approval": True,
            }
        ]
        stages.extend(
            {
                "name": stage,
                "status": "planned",
                "requires_approval": stage
                in {"frontmatter", "introduction", "image_bank", "cover"},
            }
            for stage in COMMON_STAGES
        )
        editions.append(
            {
                "locale": profile.locale,
                "language": profile.language,
                "pipeline_language": profile.pipeline_language,
                "label": profile.label,
                "end_marker": profile.end_marker,
                "source_action": profile.source_action,
                "stages": stages,
            }
        )

    payload = {
        "schema_version": AUTOMATED_SCHEMA_VERSION,
        "mode": "automated",
        "pilot": "en-gb_pt-br",
        "read_only": True,
        "status": "blocked" if errors else "ready",
        "source": {
            "batch_code": str(_value(batch, "code")),
            "item_id": _value(item, "id", None),
            "book_code": book_code,
            "title": title,
            "author": author,
            "original_year": _value(item, "original_year", None),
            "source_language": source_language,
            "source_status": source_status,
            "source_path": str(_value(item, "original_path")),
            "clean_path": str(_value(item, "clean_path")),
            "source_sha256": str(_value(item, "source_sha256")),
        },
        "preparation_stages": preparation_stages,
        "editions": editions,
        "errors": errors,
        "warnings": warnings,
    }
    payload["plan_sha256"] = _plan_sha256(payload)
    return payload
