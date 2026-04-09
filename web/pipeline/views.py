import json
import logging
import os
import sys
from pathlib import Path
import shutil
import tempfile
from datetime import datetime
import hashlib
import re
import zipfile
from urllib.parse import urlencode

from django.conf import settings
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.db import IntegrityError, connection, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from editorial.models import (
    Contributor,
    EditionBuild,
    Edition as EditorialEdition,
    EditionPipeline,
    EditionText,
    Language,
    PipelineStage,
    Seal,
    Work,
)
from editorial import kdp_mode
from editorial.frontmatter import optional_section_warnings
from gaiden.application.pipeline import ingest as pipeline_ingest
from gaiden.application.pipeline import normalization as pipeline_normalization
from gaiden.application.pipeline.translation import (
    chunk_truncation_reason as resolve_chunk_truncation_reason,
    run_translate_with_contract as run_translation_contract,
)
from gaiden.application.pipeline.gates import preflight_gate as resolve_preflight_gate
from gaiden.application.pipeline.status import resolve_block_status_map
from gaiden.infrastructure import storage

from .models import (
    BookEditionTemplate,
    CORE_BLOCK_KEY,
    CORE_ISOLATION_LANGUAGES,
    EDITORIAL_LANGUAGES,
    PipelineJob,
    SYSTEM_BLOCKS,
    TextSnapshot,
    ensure_bookeditiontemplate_runtime_columns,
    get_book_md_path,
)
try:
    from .models import PipelineRun, PipelineRunItem
except ImportError:
    PipelineRun = None
    PipelineRunItem = None
from .forms import BookEditionTemplateForm, BookSourceUploadForm, normalize_book_code_input
from .services import (
    book_manifest,
    build_book,
    chapter_agent,
    canonical_merge,
    core_docker,
    editorial_split,
    export_book,
    html_preprod,
    heading_cleaner,
    legacy_merges,
    md_quality,
    md_transform,
    miolo_transform,
    paths,
    preflight,
    refine_qa,
    stage_policy,
    utils,
)

SOURCE_FORMAT_HTML = "html"
SOURCE_FORMAT_TXT = "txt"
SOURCE_FORMAT_ALLOWED = {SOURCE_FORMAT_HTML, SOURCE_FORMAT_TXT}
STAGE_HTML_UPLOADED = "HTML_UPLOADED"
STAGE_TXT_UPLOADED = "TXT_UPLOADED"
STAGE_HTML_PREPROD_READY = "HTML_PREPROD_READY"
STAGE_MD_SOURCE_READY = "MD_SOURCE_READY"
REFINE_PROFILE_DEFAULT = "ingles_neutro"
REFINE_PROFILES = {
    "ingles_neutro": {
        "label": "Ingles neutro",
        "agent_name": "Aldebaran",
        "description": "Leitura neutra, comercial e mais controlada.",
        "style_directive": (
            "Target profile: neutral modern English. Prefer lexical restraint, broad trade readability, "
            "and stable narrative clarity. Avoid ornamental fantasy diction unless the source strongly demands it."
        ),
    },
    "ingles_flex": {
        "label": "Ingles flex",
        "agent_name": "Alamaguederaz",
        "description": "Magia e espada, aventura, ritmo mais elastico.",
        "style_directive": (
            "Target profile: flexible adventure English. Preserve facts strictly, but allow stronger rhythm, "
            "atmosphere, pulp-adventure energy, and sword-and-sorcery flavor when supported by the source."
        ),
    },
    "headingcleaner": {
        "label": "Inglês filosofia",
        "agent_name": "HeadingCleaner",
        "description": "Fluxo especializado para filosofia em ingles, com refine estrutural e prosa controlada.",
        "style_directive": (
            "Target profile: philosophical English cleanup. Preserve meaning, argument flow, paragraphing, and tonal gravity, "
            "while improving structural clarity, section handling, heading consistency, and modern readability. Prefer disciplined, "
            "clean, conceptually precise prose for classical and philosophical books."
        ),
    },
    "de_kaiser": {
        "label": "Deutsch Kaiser",
        "agent_name": "Kaiser",
        "description": "Refine literario em alemao com foco em fluidez, tom e registro controlado.",
        "style_directive": (
            "Target profile: controlled modern literary German. Preserve full meaning, chronology, paragraphing, "
            "and atmosphere. Prefer native, fluent German cadence without flattening period tension or detective tone."
        ),
    },
    "italiano_neutro": {
        "label": "Italiano neutro",
        "agent_name": "Aldebaran",
        "description": "Refine letterario in italiano moderno con controllo di tono, fluidita e leggibilita.",
        "style_directive": (
            "Target profile: neutral modern Italian. Preserve full meaning, chronology, paragraphing, and atmosphere. "
            "Prefer fluent, natural Italian prose with controlled rhythm and commercial readability, without flattening "
            "literary tension or period detail."
        ),
    },
}

BLOCK_STATUS_LABELS = {
    "bloco_01_ready": "bloco_01_ready",
    "bloco_02_running": "bloco_02_running",
    "bloco_02_done": "bloco_02_done",
    "bloco_03_ready": "bloco_03_ready",
    "bloco_03_done": "bloco_03_done",
    "bloco_04_done": "bloco_04_done",
}
REFINE_RETURN_DIRNAME = "return_aldebaran"
POLISH_RETURN_DIRNAME = "return_bismarck"
TRANSLATE_VARIANT_OPTIONS = (
    {"value": "en", "label": "EN (modern)", "base_language": "en"},
    {"value": "en_philo", "label": "English-Philosofer", "base_language": "en"},
    {"value": "en_devotional", "label": "English-Devotional", "base_language": "en"},
    {"value": "es", "label": "ES", "base_language": "es"},
    {"value": "ptbr", "label": "PT-BR", "base_language": "ptbr"},
    {"value": "de", "label": "DE", "base_language": "de"},
    {"value": "fr", "label": "FR", "base_language": "fr"},
    {"value": "it", "label": "IT", "base_language": "it"},
)
_TRANSLATE_VARIANT_LABELS = {item["value"]: item["label"] for item in TRANSLATE_VARIANT_OPTIONS}
_TRANSLATE_VARIANT_BASES = {item["value"]: item["base_language"] for item in TRANSLATE_VARIANT_OPTIONS}
_TRANSLATE_VARIANT_ALIASES = {
    "": "en",
    "en": "en",
    "english": "en",
    "en_philo": "en_philo",
    "en-philo": "en_philo",
    "enphilo": "en_philo",
    "english-philosofer": "en_philo",
    "english_philosofer": "en_philo",
    "englishphilosofer": "en_philo",
    "english-philosopher": "en_philo",
    "english_philosopher": "en_philo",
    "englishphilosopher": "en_philo",
    "en_devotional": "en_devotional",
    "en-devotional": "en_devotional",
    "endevotional": "en_devotional",
    "english-devotional": "en_devotional",
    "english_devotional": "en_devotional",
    "englishdevotional": "en_devotional",
}

_HTML_STAGE_ORDER = {
    PipelineStage.RAW: 10,
    STAGE_HTML_UPLOADED: 20,
    STAGE_HTML_PREPROD_READY: 30,
    STAGE_MD_SOURCE_READY: 40,
    PipelineStage.NORMALIZED: 50,
}


def _normalize_translate_variant(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in _TRANSLATE_VARIANT_ALIASES:
        return _TRANSLATE_VARIANT_ALIASES[raw]
    compact = raw.replace(" ", "").replace("-", "_")
    if compact in _TRANSLATE_VARIANT_ALIASES:
        return _TRANSLATE_VARIANT_ALIASES[compact]
    normalized = utils.normalize_lang(value)
    return normalized if normalized in _TRANSLATE_VARIANT_LABELS else normalized or "en"


def _translate_base_language(value: str | None) -> str:
    variant = _normalize_translate_variant(value)
    return _TRANSLATE_VARIANT_BASES.get(variant, utils.normalize_lang(variant))


def _translate_variant_label(value: str | None) -> str:
    variant = _normalize_translate_variant(value)
    return _TRANSLATE_VARIANT_LABELS.get(variant, variant.upper())


def _recommended_split_parts_for_translate_variant(value: str | None) -> int:
    variant = _normalize_translate_variant(value)
    if variant in {"en_philo", "en_devotional"}:
        return 4
    return 1

logger = logging.getLogger(__name__)


def _normalize_source_format(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in SOURCE_FORMAT_ALLOWED:
        return SOURCE_FORMAT_TXT
    return normalized


def _normalize_refine_profile(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in REFINE_PROFILES:
        return REFINE_PROFILE_DEFAULT
    return normalized


def _refine_profile_config(value: str | None) -> dict[str, str]:
    return REFINE_PROFILES[_normalize_refine_profile(value)]


def _default_refine_profile_for_language(language: str | None) -> str:
    normalized = utils.normalize_lang(language)
    if normalized == "de":
        return "de_kaiser"
    if normalized == "it":
        return "italiano_neutro"
    return REFINE_PROFILE_DEFAULT


def _refine_profile_keys_for_language(language: str | None) -> tuple[str, ...]:
    normalized = utils.normalize_lang(language)
    if normalized == "de":
        return ("de_kaiser",)
    if normalized == "it":
        return ("italiano_neutro",)
    return ("ingles_neutro", "ingles_flex", "headingcleaner")


def _normalized_refine_profile_for_language(value: str | None, language: str | None) -> str:
    normalized = _normalize_refine_profile(value)
    allowed = _refine_profile_keys_for_language(language)
    if normalized not in allowed:
        return _default_refine_profile_for_language(language)
    return normalized


def _read_json_dict(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _preflight_step_status(preflight_json: Path) -> tuple[str, str, str]:
    report = _read_json_dict(preflight_json)
    if not report:
        return (
            "warn",
            "revisar",
            "Relatorio PRE_FLIGHT.json ilegivel ou vazio; confira manualmente antes do MD final.",
        )

    critical = [str(item).strip() for item in report.get("critical", []) if str(item).strip()]
    medium = [str(item).strip() for item in report.get("medium", []) if str(item).strip()]
    light = [str(item).strip() for item in report.get("light", []) if str(item).strip()]
    all_issues = critical + medium + light
    fallback_detected = any(
        ("fallback" in item.lower()) or ("timeout" in item.lower()) or ("timed out" in item.lower())
        for item in all_issues
    )

    if critical or medium or light:
        parts = []
        if critical:
            parts.append(f"{len(critical)} critico(s)")
        if medium:
            parts.append(f"{len(medium)} medio(s)")
        if light:
            parts.append(f"{len(light)} leve(s)")
        summary = ", ".join(parts)
        if fallback_detected:
            summary = f"{summary}; houve fallback/timeout da IA"
        return (
            "warn",
            "revisar",
            f"Relatorio com alertas: {summary}. Nao tratar como aprovacao silenciosa.",
        )

    verdict_reason = str(report.get("verdict_reason") or "").strip()
    if verdict_reason:
        return ("ok", "OK", f"Veredito: {verdict_reason}")
    return ("ok", "OK", "")


def _source_format_from_template(template: BookEditionTemplate | None) -> str:
    if template is None:
        return SOURCE_FORMAT_TXT
    return _normalize_source_format(getattr(template, "text_source_mode", SOURCE_FORMAT_TXT))


def _post_field(post_data, *keys: str) -> str:
    for key in keys:
        value = (post_data.get(key) or "").strip()
        if value:
            return value
    return ""


def _running_under_tests() -> bool:
    return "test" in sys.argv


def _require_postgres_ingest_runtime():
    if _running_under_tests():
        return None
    if connection.vendor == "postgresql":
        return None
    return HttpResponse(
        (
            "Cadastro bloqueado: o runtime web oficial deve usar PostgreSQL. "
            "Suba o Django com PGHOST/PGDATABASE/PGUSER/PGPASSWORD exportados "
            "ou use gaiden_portal.settings apenas com Postgres. "
            "Atalho local: ./run_gaiden.sh"
        ),
        status=503,
    )


def _canonicalize_ingest_post_data(post_data):
    normalized = post_data.copy()
    if not normalized.get("book_code"):
        normalized["book_code"] = _post_field(post_data, "book_id", "book")
    if not normalized.get("language"):
        normalized["language"] = _post_field(post_data, "lang")
    if not normalized.get("author_name"):
        normalized["author_name"] = _post_field(post_data, "author")
    if not normalized.get("publication_year"):
        normalized["publication_year"] = "2026"
    posted_book_code = (normalized.get("book_code") or "").strip()
    if posted_book_code:
        normalized["book_code"] = normalize_book_code_input(posted_book_code)
    posted_language = (normalized.get("language") or "").strip()
    normalized["language"] = utils.normalize_lang(posted_language) if posted_language else ""
    return normalized


def _allowed_upload_exts(source_format: str) -> set[str]:
    if source_format == SOURCE_FORMAT_HTML:
        return {".html", ".htm"}
    return {".txt"}


def _is_allowed_source_upload(source_format: str, source_file) -> bool:
    uploaded_ext = (Path(source_file.name).suffix or "").lower()
    return uploaded_ext in _allowed_upload_exts(source_format)


def _validate_ingest_v1_request(post_data, files) -> str:
    source_format = (post_data.get("source_format") or "").strip().lower()
    if source_format not in SOURCE_FORMAT_ALLOWED:
        raise ValidationError("source_format invalido. Use 'html' ou 'txt'.")

    required_fields = ("book_code", "language", "title", "author_name")
    for field in required_fields:
        if not (post_data.get(field) or "").strip():
            raise ValidationError(f"Campo obrigatorio ausente: {field}.")

    source_file = files.get("source_file")
    if source_file is None:
        raise ValidationError("Campo obrigatorio ausente: source_file.")

    if not _is_allowed_source_upload(source_format, source_file):
        allowed_exts = ", ".join(sorted(_allowed_upload_exts(source_format)))
        raise ValidationError(
            f"Arquivo invalido para '{source_format}'. Aceitos: {allowed_exts}."
        )

    return source_format


def _validate_registration_request(post_data) -> None:
    required_fields = ("book_code", "language", "title", "author_name")
    for field in required_fields:
        if not (post_data.get(field) or "").strip():
            raise ValidationError(f"Campo obrigatorio ausente: {field}.")


def _write_uploaded_file_atomic(dest_path: Path, uploaded_file) -> dict[str, object]:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".upload_", dir=str(dest_path.parent))
    temp_path = Path(temp_name)
    sha256 = hashlib.sha256()
    total_size = 0
    try:
        with os.fdopen(fd, "wb") as temp_fp:
            for chunk in uploaded_file.chunks():
                temp_fp.write(chunk)
                sha256.update(chunk)
                total_size += len(chunk)
        os.replace(temp_path, dest_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return {"sha256": sha256.hexdigest(), "size": total_size}


def _language_defaults(language_code: str) -> dict[str, str]:
    labels = {
        "en": "English",
        "ptbr": "Portugues (Brasil)",
        "es": "Espanol",
        "de": "Deutsch",
        "fr": "Francais",
        "it": "Italiano",
    }
    label = labels.get(language_code, language_code)
    return {"name": label, "native_name": label}


def _insert_edition_row_legacy_schema(
    *,
    work_id: int,
    language_id: int,
    seal_id: int,
    language_code: str,
    seal_name: str,
    template: BookEditionTemplate,
    author_name: str,
) -> None:
    now = timezone.now()
    parsed_book_id = _parse_book_id(template.book_code)
    candidate_values: dict[str, object] = {
        # Some legacy schemas require this explicit FK-like integer.
        "book_id": parsed_book_id if parsed_book_id is not None else work_id,
        "work_id": work_id,
        "language_id": language_id,
        "seal_id": seal_id,
        "publisher": template.imprint_name or "",
        "edition_year": template.edition_year,
        "raw_source_path": "",
        "title": template.title or template.book_code,
        "subtitle": template.subtitle or "",
        "author": author_name,
        "adapter": template.adapter_name or "",
        "translator": template.translator_name or "",
        "editor": template.editor_name or "",
        "about_edition_text": "",
        "publication_year": template.publication_year or 2026,
        "city": template.city_name or "Rio de Janeiro",
        "country": template.country_name or "Brasil",
        "imprint_name": template.imprint_name or "RinoBooks",
        "seal_name": seal_name,
        "frontispiece_template": template.frontispiece_text or "",
        "copyright_template": template.copyright_text or "",
        "about_edition_template": template.about_edition_text or "",
        "about_contributor_template": template.about_contributor_text or "",
        "cover_filepath": "",
        "language_code": language_code,
        "language_variant": language_code,
        "lock_translate": False,
        "lock_refine": False,
        "lock_polish": False,
        "miolo_source_stage": "",
        "created_at": now,
        "updated_at": now,
    }
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT kcu.column_name, ccu.table_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = current_schema()
              AND tc.table_name = 'edition'
            """
        )
        fk_map = {row[0]: row[1] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT column_name, is_nullable, column_default, data_type, udt_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'edition'
            ORDER BY ordinal_position
            """
        )
        rows = cursor.fetchall()
        table_columns = {row[0] for row in rows}
        has_book_id = "book_id" in table_columns
        if has_book_id and (
            "book_id" not in candidate_values or candidate_values.get("book_id") in (None, "")
        ):
            candidate_values["book_id"] = work_id

        # Fill unknown NOT NULL columns without default when possible.
        for column_name, is_nullable, column_default, data_type, udt_name in rows:
            if column_name in candidate_values:
                continue
            if column_name == "id":
                continue
            if is_nullable != "NO" or column_default is not None:
                continue
            if column_name.endswith("_id"):
                ref_table = fk_map.get(column_name, "")
                if column_name == "book_id":
                    candidate_values[column_name] = _parse_book_id(template.book_code) or work_id
                    continue
                if ref_table == "work":
                    candidate_values[column_name] = work_id
                    continue
                if ref_table == "language":
                    candidate_values[column_name] = language_id
                    continue
                if ref_table == "seal":
                    candidate_values[column_name] = seal_id
                    continue
                continue
            if column_name == "language_variant":
                candidate_values[column_name] = language_code
                continue
            if data_type in {"character varying", "text"}:
                candidate_values[column_name] = ""
            elif data_type == "boolean":
                candidate_values[column_name] = False
            elif data_type in {"integer", "smallint", "bigint"}:
                candidate_values[column_name] = 0
            elif data_type in {"timestamp without time zone", "timestamp with time zone", "date"}:
                candidate_values[column_name] = now
            elif data_type == "ARRAY":
                if udt_name in {"_varchar", "_text"}:
                    candidate_values[column_name] = []
            elif data_type in {"json", "jsonb"}:
                candidate_values[column_name] = "{}"

        insert_columns = [name for name in candidate_values if name in table_columns]
        if has_book_id and "book_id" not in insert_columns:
            insert_columns = ["book_id", *insert_columns]
        placeholders = ", ".join(["%s"] * len(insert_columns))
        values = [candidate_values[name] for name in insert_columns]
        columns_sql = ", ".join(insert_columns)
        cursor.execute(
            f"INSERT INTO edition ({columns_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
            values,
        )


def _insert_work_row_legacy_schema(
    *,
    book_code: str,
    title: str,
    language_id: int,
    language_code: str,
    author_id: int,
    publisher: str,
    year: int,
) -> None:
    now = timezone.now()
    normalized_lang = utils.normalize_lang(language_code)
    candidate_values: dict[str, object] = {
        "code": book_code,
        "title": title or book_code,
        "subtitle": "",
        "publisher": publisher or "",
        "year": year or 2026,
        "is_public_domain": True,
        "original_language_id": language_id,
        "author_id": author_id,
    }

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT kcu.column_name, ccu.table_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = current_schema()
              AND tc.table_name = 'work'
            """
        )
        fk_map = {row[0]: row[1] for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT column_name, is_nullable, column_default, data_type, udt_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'work'
            ORDER BY ordinal_position
            """
        )
        rows = cursor.fetchall()
        table_columns = {row[0] for row in rows}

        for column_name, is_nullable, column_default, data_type, udt_name in rows:
            if column_name in candidate_values or column_name == "id":
                continue
            if is_nullable != "NO" or column_default is not None:
                continue

            if column_name.endswith("_id"):
                ref_table = fk_map.get(column_name, "")
                if ref_table == "language":
                    candidate_values[column_name] = language_id
                    continue
                if ref_table == "contributor":
                    candidate_values[column_name] = author_id
                    continue
                continue

            if column_name in {"subtitle"}:
                candidate_values[column_name] = ""
                continue
            if column_name in {"language_code", "original_language_code", "language_variant"}:
                candidate_values[column_name] = normalized_lang
                continue
            if column_name == "enabled_languages":
                if data_type == "ARRAY" and udt_name in {"_varchar", "_text"}:
                    candidate_values[column_name] = [normalized_lang]
                elif data_type in {"json", "jsonb"}:
                    candidate_values[column_name] = json.dumps([normalized_lang])
                else:
                    candidate_values[column_name] = normalized_lang
                continue
            if data_type in {"character varying", "text"}:
                candidate_values[column_name] = ""
            elif data_type == "boolean":
                candidate_values[column_name] = False
            elif data_type in {"integer", "smallint", "bigint"}:
                candidate_values[column_name] = 0
            elif data_type in {"timestamp without time zone", "timestamp with time zone", "date"}:
                candidate_values[column_name] = now
            elif data_type == "ARRAY" and udt_name in {"_varchar", "_text"}:
                candidate_values[column_name] = []
            elif data_type in {"json", "jsonb"}:
                candidate_values[column_name] = json.dumps({})

        insert_columns = [name for name in candidate_values if name in table_columns]
        placeholders = ", ".join(["%s"] * len(insert_columns))
        values = [candidate_values[name] for name in insert_columns]
        columns_sql = ", ".join(insert_columns)
        cursor.execute(
            f"INSERT INTO work ({columns_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
            values,
        )


def _ensure_editorial_edition(template: BookEditionTemplate) -> tuple[EditorialEdition, bool]:
    book_code = template.book_code
    language_code = utils.normalize_lang(template.language)

    language_obj, _ = Language.objects.get_or_create(
        code=language_code,
        defaults=_language_defaults(language_code),
    )
    author_name = (template.author_name or "").strip() or "Unknown Author"
    author_obj, _ = Contributor.objects.get_or_create(
        name=author_name,
        defaults={"role": "AUTHOR"},
    )
    try:
        # Keep a local savepoint so legacy-schema fallbacks can continue inside
        # the outer cadastro transaction after an IntegrityError.
        with transaction.atomic():
            work_obj, _ = Work.objects.get_or_create(
                code=book_code,
                defaults={
                    "title": template.title or book_code,
                    "original_language": language_obj,
                    "author": author_obj,
                    "publisher": template.imprint_name or "",
                    "year": template.publication_year or 2026,
                    "is_public_domain": True,
                },
            )
    except IntegrityError:
        work_obj = Work.objects.filter(code=book_code).first()
        if work_obj is None:
            _insert_work_row_legacy_schema(
                book_code=book_code,
                title=template.title or book_code,
                language_id=language_obj.id,
                language_code=language_code,
                author_id=author_obj.id,
                publisher=template.imprint_name or "",
                year=template.publication_year or 2026,
            )
            work_obj = Work.objects.get(code=book_code)

    existing_edition = (
        EditorialEdition.objects.select_related("work", "language", "seal")
        .filter(work__code=book_code, language__code=language_code)
        .first()
    )
    if existing_edition:
        return existing_edition, False

    seal_name = (template.seal_name or "").strip() or "MantaQuest"
    seal_slug = slugify(seal_name) or "mantaquest"
    seal_obj, _ = Seal.objects.get_or_create(
        slug=seal_slug,
        defaults={"name": seal_name},
    )

    try:
        # Same rationale as Work creation: rollback only this create attempt.
        with transaction.atomic():
            edition = EditorialEdition.objects.create(
                work=work_obj,
                language=language_obj,
                seal=seal_obj,
                publisher=template.imprint_name or "",
                edition_year=template.edition_year,
                title=template.title or work_obj.title,
                subtitle=template.subtitle,
                author=author_name,
                adapter=template.adapter_name,
                translator=template.translator_name,
                editor=template.editor_name,
                publication_year=template.publication_year or 2026,
                city=template.city_name or "Rio de Janeiro",
                country=template.country_name or "Brasil",
                imprint_name=template.imprint_name or "RinoBooks",
                seal_name=seal_name,
                language_code=language_code,
                frontispiece_template=template.frontispiece_text,
                copyright_template=template.copyright_text,
                about_edition_template=template.about_edition_text,
                about_contributor_template=template.about_contributor_text,
            )
    except IntegrityError:
        edition = (
            EditorialEdition.objects.select_related("work", "language", "seal")
            .filter(work=work_obj, language=language_obj, seal=seal_obj)
            .order_by("-id")
            .first()
        )
        if edition is None:
            _insert_edition_row_legacy_schema(
                work_id=work_obj.id,
                language_id=language_obj.id,
                seal_id=seal_obj.id,
                language_code=language_code,
                seal_name=seal_name,
                template=template,
                author_name=author_name,
            )
            edition = (
                EditorialEdition.objects.select_related("work", "language", "seal")
                .filter(work=work_obj, language=language_obj, seal=seal_obj)
                .order_by("-id")
                .first()
            )
            if edition is None:
                raise
    return edition, True


def _html_artifact_paths(book_code: str, language: str, source_format: str) -> dict[str, Path]:
    language = utils.normalize_lang(language)
    root = Path(settings.BASE_DIR).parent
    abs_paths = html_preprod.artifact_paths(book_code, language)
    if source_format == SOURCE_FORMAT_HTML:
        source_original = abs_paths["raw_html"]
        if not source_original.exists() and abs_paths["raw_htm"].exists():
            source_original = abs_paths["raw_htm"]
    else:
        source_original = abs_paths["raw_html"]

    def _rel(path: Path) -> Path:
        try:
            return path.relative_to(root)
        except ValueError:
            return path

    return {
        "raw_html_path": _rel(abs_paths["raw_html"]),
        "source_original": _rel(source_original),
        "preprod_clean_html": _rel(abs_paths["preprod_clean_html"]),
        "preprod_report_json": _rel(abs_paths["preprod_report_json"]),
        "md_source": _rel(abs_paths["md_source"]),
        "md_normalized": _rel(abs_paths["md_normalized"]),
        "md_canonical": _rel(abs_paths["md_canonical"]),
    }


def _html_stage_rank(stage: str | None) -> int:
    return _HTML_STAGE_ORDER.get((stage or "").strip(), 0)


def pipeline_dashboard(request):
    pipelines = EditionPipeline.objects.select_related("edition__work", "edition__language").order_by("edition__work__code")
    return render(request, "pipeline/dashboard.html", {"pipelines": pipelines})


def pipeline_home(request):
    editions = list(
        EditorialEdition.objects.select_related("work", "language", "seal")
        .order_by("work__code", "language__code")
    )
    pipeline_map = {
        row.edition_id: row
        for row in EditionPipeline.objects.select_related("edition").filter(
            edition_id__in=[ed.id for ed in editions]
        )
    }
    edition_rows = [
        {
            "edition": ed,
            "stage": (pipeline_map.get(ed.id).current_stage if pipeline_map.get(ed.id) else "RAW"),
            "last_log": (pipeline_map.get(ed.id).last_log if pipeline_map.get(ed.id) else ""),
        }
        for ed in editions
    ]
    if PipelineRun is not None:
        recent_runs = list(PipelineRun.objects.prefetch_related("items").order_by("-id")[:10])
    else:
        recent_runs = []
    book_0008 = next(
        (ed for ed in editions if ed.work.code == "book_0008" and utils.normalize_lang(ed.language.code) == "en"),
        None,
    )
    return render(
        request,
        "pipeline/home.html",
        {
            "edition_rows": edition_rows,
            "recent_runs": recent_runs,
            "book_0008": book_0008,
            "edition_count": len(editions),
        },
    )


def pipeline_jobs(request):
    pipelines = EditionPipeline.objects.select_related("edition__work", "edition__language").order_by("-id")
    return render(request, "pipeline/jobs.html", {"pipelines": pipelines})


def _template_upload_url(template: BookEditionTemplate | None) -> str:
    if template is None:
        return ""
    return reverse(
        "book_edition_upload",
        kwargs={"book_code": template.book_code, "language": utils.normalize_lang(template.language)},
    )


def _template_reedit_html_url(template: BookEditionTemplate | None, edition: EditorialEdition | None = None) -> str:
    book_code = ""
    language = ""
    if template is not None:
        book_code = template.book_code
        language = utils.normalize_lang(template.language)
    elif edition is not None:
        book_code = edition.work.code
        language = utils.normalize_lang(edition.language.code)
    if not book_code or not language:
        return ""
    return (
        reverse(
            "book_edition_upload",
            kwargs={"book_code": book_code, "language": language},
        )
        + "?force_source_format=html"
    )


def _existing_source_info(template: BookEditionTemplate | None, edition: EditorialEdition | None = None) -> dict[str, object]:
    path_value = ""
    if template and (template.source_saved_path or "").strip():
        path_value = template.source_saved_path.strip()
    elif edition and (edition.raw_source_path or "").strip():
        path_value = edition.raw_source_path.strip()

    resolved_path = _resolve_project_path(path_value) if path_value else None
    return {
        "path": path_value,
        "exists": bool(resolved_path and resolved_path.exists()),
        "name": (template.source_original_name if template else "") or (resolved_path.name if resolved_path else ""),
        "uploaded_at": template.source_uploaded_at if template else None,
        "sha256": (template.source_file_sha256 if template else "") or "",
        "size": template.source_file_size if template else None,
    }


def _registration_status_label(template: BookEditionTemplate | None) -> str:
    if template is None:
        return "Nao cadastrado"
    return dict(BookEditionTemplate.REGISTRATION_STATUS_CHOICES).get(template.registration_status, template.registration_status)


def _book_registration_rows() -> list[dict[str, object]]:
    ensure_bookeditiontemplate_runtime_columns()
    editions = list(
        EditorialEdition.objects.select_related("work", "language", "seal")
        .order_by("work__code", "language__code")
    )
    pipeline_map = {
        row.edition_id: row
        for row in EditionPipeline.objects.select_related("edition").filter(
            edition_id__in=[ed.id for ed in editions]
        )
    }
    template_map = {
        (row.book_code, utils.normalize_lang(row.language)): row
        for row in BookEditionTemplate.objects.all()
    }

    rows: list[dict[str, object]] = []
    for edition in editions:
        book_code = edition.work.code
        language = utils.normalize_lang(edition.language.code)
        template = template_map.get((book_code, language))
        pipeline_state = pipeline_map.get(edition.id)
        source_format = _source_format_from_template(template)
        current_stage = (pipeline_state.current_stage if pipeline_state else "") or ""
        if (
            source_format == SOURCE_FORMAT_HTML
            and current_stage
            and _html_stage_rank(current_stage) > 0
        ):
            steps_url = reverse("pipeline_html_dashboard", kwargs={"edition_id": edition.id})
        else:
            steps_url = reverse("edition_steps", kwargs={"edition_id": edition.id})
        rows.append(
            {
                "edition": edition,
                "template": template,
                "status_label": _registration_status_label(template),
                "source_info": _existing_source_info(template, edition),
                "edit_url": reverse("edition_steps", kwargs={"edition_id": edition.id}),
                "upload_url": _template_upload_url(template),
                "reedit_html_url": _template_reedit_html_url(template, edition),
                "steps_url": steps_url,
            }
        )
    return rows


def _registration_book_options(rows: list[dict[str, object]]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for row in rows:
        edition = row["edition"]
        parsed = _parse_book_id(edition.work.code)
        book_number = f"{parsed:02d}" if parsed is not None else edition.work.code
        options.append(
            {
                "value": edition.work.code,
                "label": f"{book_number} - {edition.work.code} [{utils.normalize_lang(edition.language.code)}] - {edition.title or edition.work.title}",
            }
        )
    return options


def _filter_registration_rows(
    rows: list[dict[str, object]],
    *,
    book_filter: str,
) -> tuple[list[dict[str, object]], bool]:
    book_value = normalize_book_code_input((book_filter or "").strip())
    if not book_value:
        return [], False
    filtered = [row for row in rows if row["edition"].work.code == book_value]
    return filtered, True


def _render_registration_page(request, form, *, template=None, status=200):
    selected_source_format = _normalize_source_format(
        template.text_source_mode if template else SOURCE_FORMAT_TXT
    )
    all_rows = _book_registration_rows()
    book_filter = (request.GET.get("book") or "").strip()
    filtered_rows, show_rows_table = _filter_registration_rows(
        all_rows,
        book_filter=book_filter,
    )
    return render(
        request,
        "pipeline/book_edition_form.html",
        {
            "form": form,
            "source_format": selected_source_format,
            "continue_options": _continue_book_options(),
            "registration_book_options": _registration_book_options(all_rows),
            "edition_rows": filtered_rows,
            "show_rows_table": show_rows_table,
            "book_filter_value": book_filter,
            "current_template": template,
            "upload_url": _template_upload_url(template),
            "status_label": _registration_status_label(template),
            "source_info": _existing_source_info(template),
        },
        status=status,
    )


def _save_template_and_edition_metadata(template: BookEditionTemplate) -> tuple[EditorialEdition, bool]:
    template.save()
    edition, edition_created = _ensure_editorial_edition(template)

    edition.title = template.title
    edition.subtitle = template.subtitle
    edition.author = template.author_name
    edition.adapter = template.adapter_name
    edition.translator = template.translator_name
    edition.editor = template.editor_name
    edition.publication_year = template.publication_year
    edition.city = template.city_name or edition.city
    edition.country = template.country_name or edition.country
    edition.imprint_name = template.imprint_name or edition.imprint_name
    edition.seal_name = template.seal_name or edition.seal_name
    if template.imprint_name:
        edition.publisher = template.imprint_name
    edition.frontispiece_template = template.frontispiece_text
    edition.copyright_template = template.copyright_text
    edition.about_edition_template = template.about_edition_text
    edition.about_contributor_template = template.about_contributor_text
    edition.save()
    return edition, edition_created


def _handle_source_upload(
    request,
    template: BookEditionTemplate,
    upload_form: BookSourceUploadForm,
    *,
    edition: EditorialEdition | None = None,
):
    selected_source_format = _normalize_source_format(upload_form.cleaned_data["source_format"])
    source_file = upload_form.cleaned_data["source_file"]
    replace_existing = bool(upload_form.cleaned_data.get("replace_existing"))
    root = Path(settings.BASE_DIR).parent
    edition_created = False
    if edition is None:
        edition, edition_created = _ensure_editorial_edition(template)
    language = utils.normalize_lang(template.language)
    ext = ".html" if selected_source_format == SOURCE_FORMAT_HTML else ".txt"
    raw_path = root / "data" / "raw" / template.book_code / f"{template.book_code}_{language}_raw{ext}"
    previous_source = _existing_source_info(template, edition)
    upload_meta = _write_uploaded_file_atomic(raw_path, source_file)

    with transaction.atomic():
        template.text_source_mode = selected_source_format
        template.registration_status = BookEditionTemplate.STATUS_READY_FOR_BLOCK_02
        template.source_file_type = selected_source_format
        template.source_original_name = source_file.name
        template.source_saved_path = str(raw_path)
        template.source_file_size = int(upload_meta["size"])
        template.source_uploaded_at = timezone.now()
        template.source_file_sha256 = str(upload_meta["sha256"])
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            template.source_uploaded_by = request.user.get_username()
        template.save()

        edition.title = template.title
        edition.subtitle = template.subtitle
        edition.author = template.author_name
        edition.adapter = template.adapter_name
        edition.translator = template.translator_name
        edition.editor = template.editor_name
        edition.publication_year = template.publication_year
        edition.city = template.city_name or edition.city
        edition.country = template.country_name or edition.country
        edition.imprint_name = template.imprint_name or edition.imprint_name
        edition.seal_name = template.seal_name or edition.seal_name
        edition.raw_source_path = str(raw_path)
        edition.save()

        texts, _ = EditionText.objects.get_or_create(edition=edition)
        texts.raw_path = str(raw_path)
        texts.save(update_fields=["raw_path", "updated_at"])

        pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
        pipeline_state.raw_at = timezone.now()
        pipeline_state.current_stage = (
            STAGE_HTML_UPLOADED if selected_source_format == SOURCE_FORMAT_HTML else STAGE_TXT_UPLOADED
        )
        replacement_note = ""
        if previous_source["path"] and replace_existing:
            replacement_note = f" :: replaced={previous_source['path']}"
        pipeline_state.last_log = (
            f"{timezone.now().isoformat()} :: {pipeline_state.current_stage} :: source_upload :: {raw_path}"
            f"{replacement_note}"
        )
        pipeline_state.save(update_fields=["raw_at", "current_stage", "last_log"])

        kdp_mode.build_frontmatter_files(edition, storage.frontmatter_dir())

    logger.info(
        "pipeline_ingest_v1",
        extra={
            "book_code": template.book_code,
            "language": language,
            "source_format": selected_source_format,
            "stage": pipeline_state.current_stage,
            "raw_path": str(raw_path),
            "replaced": bool(previous_source["path"] and replace_existing),
            "result": "ok",
        },
    )
    messages.success(request, f"Arquivo-fonte salvo para {template.book_code} [{language}].")
    if previous_source["path"] and replace_existing:
        messages.info(request, f"Substituicao registrada. Arquivo anterior: {previous_source['path']}")
    if edition_created:
        messages.info(request, f"Edicao editorial criada automaticamente para {template.book_code} [{language}].")
    if selected_source_format == SOURCE_FORMAT_HTML:
        return redirect("pipeline_html_dashboard", edition_id=edition.id)
    return redirect("edition_steps", edition_id=edition.id)


def _refine_return_dirname(refine_profile: str | None = None, target_language: str | None = None) -> str:
    profile_cfg = _refine_profile_config(refine_profile)
    agent_slug = slugify(profile_cfg["agent_name"]).replace("-", "_")
    if agent_slug:
        return f"return_{agent_slug}"
    normalized_lang = utils.normalize_lang(target_language)
    if normalized_lang == "de":
        return "return_kaiser"
    return REFINE_RETURN_DIRNAME


def _resolve_refine_output_dir(
    source_dir: Path | None,
    *,
    refine_profile: str | None = None,
    target_language: str | None = None,
) -> Path | None:
    if source_dir is None:
        return None
    dirname = _refine_return_dirname(refine_profile, target_language)
    if source_dir.name == "parts" and source_dir.parent.name == "split_by_chapter":
        candidates = [source_dir.parent / dirname]
    else:
        candidates = [
            source_dir / dirname,
            source_dir.parent / dirname,
        ]
    return next((path for path in candidates if path.exists()), candidates[0])


def book_edition_list(request):
    return book_edition_edit(request)


def pipeline_html_dashboard(request, edition_id: int):
    edition = get_object_or_404(EditorialEdition, id=edition_id)
    book_code, language = _edition_codes(edition)
    language = utils.normalize_lang(language)
    root = Path(settings.BASE_DIR).parent

    template = (
        BookEditionTemplate.objects.filter(book_code=book_code, language=language).first()
    )
    source_format = _source_format_from_template(template)
    if source_format != SOURCE_FORMAT_HTML:
        messages.info(request, "Esta edicao esta configurada como TXT. Seguindo para o pipeline comum.")
        return redirect("edition_steps", edition_id=edition.id)

    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
    artifacts = _html_artifact_paths(book_code, language, source_format)
    report_payload = None
    report_errors: list[str] = []
    report_path_abs = root / artifacts["preprod_report_json"]
    if report_path_abs.exists():
        try:
            report_payload = json.loads(report_path_abs.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            report_errors.append("Report JSON invalido. Rode novamente o preprod.")

    raw_source_abs = None
    if (edition.raw_source_path or "").strip():
        raw_source_abs = Path(edition.raw_source_path.strip())
        if not raw_source_abs.is_absolute():
            raw_source_abs = root / raw_source_abs

    stage_rank = _html_stage_rank(pipeline_state.current_stage)
    step1_ok = stage_rank >= _html_stage_rank(STAGE_HTML_UPLOADED)
    report_ok = bool(report_payload and report_payload.get("ok_to_convert") is True)
    clean_ready = (root / artifacts["preprod_clean_html"]).exists()
    can_run_preprod = (root / artifacts["source_original"]).exists() or bool(raw_source_abs and raw_source_abs.exists())
    can_run_convert = (
        stage_rank >= _html_stage_rank(STAGE_HTML_PREPROD_READY)
        and clean_ready
        and report_ok
    )

    artifact_rows = [
        {
            "label": "Original",
            "path": str(artifacts["source_original"]),
            "exists": (root / artifacts["source_original"]).exists(),
        },
        {
            "label": "Clean HTML",
            "path": str(artifacts["preprod_clean_html"]),
            "exists": (root / artifacts["preprod_clean_html"]).exists(),
        },
        {
            "label": "Preprod report",
            "path": str(artifacts["preprod_report_json"]),
            "exists": report_path_abs.exists(),
        },
        {
            "label": "Source MD",
            "path": str(artifacts["md_source"]),
            "exists": (root / artifacts["md_source"]).exists(),
        },
    ]

    pipeline01_artifact_rows = [
        {
            "label": "Normalized MD",
            "path": str(artifacts["md_normalized"]),
        },
        {
            "label": "Canonical MD",
            "path": str(artifacts["md_canonical"]),
        },
    ]

    return render(
        request,
        "pipeline/html_pipeline_dashboard.html",
        {
            "edition": edition,
            "book_code": book_code,
            "language": language,
            "lang": language,
            "source_format": source_format,
            "stage": pipeline_state.current_stage,
            "current_stage": pipeline_state.current_stage,
            "step1_ok": step1_ok,
            "last_log": pipeline_state.last_log,
            "artifacts": {key: str(value) for key, value in artifacts.items()},
            "artifact_rows": artifact_rows,
            "pipeline01_artifact_rows": pipeline01_artifact_rows,
            "raw_source_path": edition.raw_source_path,
            "report_payload": report_payload,
            "report_errors": report_errors,
            "can_run_preprod": can_run_preprod,
            "can_run_convert": can_run_convert,
            "can_continue_common": (root / artifacts["md_source"]).exists(),
        },
    )


def _ensure_html_lane(request, edition_id: int):
    edition = get_object_or_404(EditorialEdition, id=edition_id)
    book_code, language = _edition_codes(edition)
    language = utils.normalize_lang(language)
    template = BookEditionTemplate.objects.filter(book_code=book_code, language=language).first()
    source_format = _source_format_from_template(template)
    if source_format != SOURCE_FORMAT_HTML:
        messages.error(request, "Acao HTML indisponivel: source_format atual nao e HTML.")
        return None, redirect("edition_steps", edition_id=edition.id)
    return edition, None


def pipeline_html_reupload_run(request, edition_id: int):
    if request.method != "POST":
        return redirect("pipeline_html_dashboard", edition_id=edition_id)
    edition, failure = _ensure_html_lane(request, edition_id)
    if failure:
        return failure

    source_file = request.FILES.get("source_file")
    if source_file is None:
        messages.error(request, "Selecione um arquivo HTML (.html/.htm) para re-upload.")
        return redirect("pipeline_html_dashboard", edition_id=edition.id)
    if not _is_allowed_source_upload(SOURCE_FORMAT_HTML, source_file):
        messages.error(request, "Arquivo invalido para re-upload HTML. Aceitos: .html, .htm.")
        return redirect("pipeline_html_dashboard", edition_id=edition.id)

    book_code, language = _edition_codes(edition)
    language = utils.normalize_lang(language)
    root = Path(settings.BASE_DIR).parent
    artifacts = _html_artifact_paths(book_code, language, SOURCE_FORMAT_HTML)
    raw_path = root / artifacts["raw_html_path"]

    _write_uploaded_file_atomic(raw_path, source_file)

    stale_keys = (
        "preprod_clean_html",
        "preprod_report_json",
        "md_source",
        "md_normalized",
        "md_canonical",
    )
    removed_paths: list[str] = []
    for key in stale_keys:
        stale_path = root / artifacts[key]
        if not stale_path.exists():
            continue
        if stale_path.is_dir():
            shutil.rmtree(stale_path)
        else:
            stale_path.unlink()
        removed_paths.append(str(stale_path))

    with transaction.atomic():
        edition.raw_source_path = str(raw_path)
        edition.save(update_fields=["raw_source_path", "updated_at"])

        texts, _ = EditionText.objects.get_or_create(edition=edition)
        texts.raw_path = str(raw_path)
        texts.save(update_fields=["raw_path", "updated_at"])

        pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
        pipeline_state.current_stage = STAGE_HTML_UPLOADED
        pipeline_state.raw_at = timezone.now()
        pipeline_state.normalized_at = None
        pipeline_state.split_at = None
        pipeline_state.chunked_at = None
        pipeline_state.translated_at = None
        pipeline_state.refined_at = None
        pipeline_state.merged_at = None
        pipeline_state.polished_at = None
        pipeline_state.miolo_md_at = None
        pipeline_state.final_md_at = None
        pipeline_state.last_log = f"{timezone.now().isoformat()} :: HTML_UPLOADED :: reupload :: {raw_path}"
        pipeline_state.save(
            update_fields=[
                "current_stage",
                "raw_at",
                "normalized_at",
                "split_at",
                "chunked_at",
                "translated_at",
                "refined_at",
                "merged_at",
                "polished_at",
                "miolo_md_at",
                "final_md_at",
                "last_log",
            ]
        )

    logger.info(
        "pipeline_ingest_v1_reupload",
        extra={
            "book_code": book_code,
            "language": language,
            "source_format": SOURCE_FORMAT_HTML,
            "stage": STAGE_HTML_UPLOADED,
            "raw_path": str(raw_path),
            "removed_artifacts": len(removed_paths),
            "result": "ok",
        },
    )
    messages.success(
        request,
        f"RAW HTML atualizado: {raw_path}. Artefatos invalidados: {len(removed_paths)}.",
    )
    return redirect("pipeline_html_dashboard", edition_id=edition.id)


def pipeline_html_preprod_run(request, edition_id: int):
    if request.method != "POST":
        return redirect("pipeline_html_dashboard", edition_id=edition_id)
    edition, failure = _ensure_html_lane(request, edition_id)
    if failure:
        return failure

    try:
        clean_path, report_path, report = html_preprod.run_html_preprod(edition)
    except Exception as exc:
        messages.error(request, f"Pre-producao HTML falhou: {exc}")
        return redirect("pipeline_html_dashboard", edition_id=edition.id)

    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
    pipeline_state.current_stage = STAGE_HTML_PREPROD_READY
    pipeline_state.last_log = f"{timezone.now().isoformat()} :: HTML_PREPROD_READY :: {clean_path}"
    pipeline_state.save(update_fields=["current_stage", "last_log"])

    if report.get("ok_to_convert"):
        messages.success(request, f"Preprod OK. clean.html gerado: {clean_path}")
    else:
        messages.warning(request, "Preprod concluido, mas gate de conversao bloqueado (ok_to_convert=false).")
    for warning in report.get("warnings", [])[:5]:
        messages.warning(request, warning)
    messages.info(request, f"Report: {report_path}")
    return redirect("pipeline_html_dashboard", edition_id=edition.id)


def pipeline_html_convert_run(request, edition_id: int):
    if request.method != "POST":
        return redirect("pipeline_html_dashboard", edition_id=edition_id)
    edition, failure = _ensure_html_lane(request, edition_id)
    if failure:
        return failure

    try:
        _, report = html_preprod.load_preprod_report(edition)
    except Exception as exc:
        messages.error(request, f"Conversao bloqueada: report de preprod indisponivel ({exc}).")
        return redirect("pipeline_html_dashboard", edition_id=edition.id)

    if report.get("ok_to_convert") is not True:
        messages.error(request, "Conversao bloqueada: report indica ok_to_convert=false.")
        return redirect("pipeline_html_dashboard", edition_id=edition.id)

    try:
        md_path, engine = html_preprod.run_html_to_md(edition)
    except Exception as exc:
        messages.error(request, f"Conversao HTML->MD falhou: {exc}")
        return redirect("pipeline_html_dashboard", edition_id=edition.id)

    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
    pipeline_state.current_stage = STAGE_MD_SOURCE_READY
    pipeline_state.core_last_txt_path = str(md_path)
    pipeline_state.last_log = f"{timezone.now().isoformat()} :: MD_SOURCE_READY :: {md_path} [{engine}]"
    pipeline_state.save(update_fields=["current_stage", "core_last_txt_path", "last_log"])

    messages.success(request, f"Conversao HTML->MD OK ({engine}): {md_path}")
    return redirect(f"{reverse('edition_steps', kwargs={'edition_id': edition.id})}?allow_html_to_common=1")


def pipeline_html_md_normalize_run(request, edition_id: int):
    if request.method != "POST":
        return redirect("pipeline_html_dashboard", edition_id=edition_id)
    edition, failure = _ensure_html_lane(request, edition_id)
    if failure:
        return failure
    messages.info(request, "Redirecionando para o Pipeline 01 (Steps comuns).")
    return redirect(f"{reverse('edition_steps', kwargs={'edition_id': edition.id})}?allow_html_to_common=1")


def book_edition_edit(request, book_code=None, language=None):
    postgres_guard = _require_postgres_ingest_runtime()
    if postgres_guard is not None:
        return postgres_guard
    ensure_bookeditiontemplate_runtime_columns()

    initial_book_code = (book_code or request.GET.get("book_code") or "").strip()
    initial_language = utils.normalize_lang((language or request.GET.get("language") or "en").strip())
    canonical_post_data = _canonicalize_ingest_post_data(request.POST) if request.method == "POST" else None

    template = None
    if initial_book_code:
        template = (
            BookEditionTemplate.objects.filter(
                book_code=initial_book_code,
                language=initial_language,
            ).first()
        )

    # When posting from the generic cadastro URL, load the existing template
    # by the submitted pair to avoid duplicate (book_code, language) creation.
    if request.method == "POST" and template is None:
        posted_book_code = (canonical_post_data.get("book_code") or "").strip()
        posted_language = utils.normalize_lang((canonical_post_data.get("language") or "").strip())
        if posted_book_code and posted_language:
            template = (
                BookEditionTemplate.objects.filter(
                    book_code=posted_book_code,
                    language=posted_language,
                ).first()
            )

    if request.method == "POST":
        action = (request.POST.get("action") or "save_metadata").strip()
        legacy_upload_requested = bool(request.FILES.get("source_file"))
        legacy_contract_submission = bool(
            "source_format" in canonical_post_data and request.POST.get("action") != "save_metadata"
        )
        if action == "upload_source" and not legacy_upload_requested:
            if template is None:
                messages.error(request, "Cadastre o livro antes de enviar o arquivo-fonte.")
                form = BookEditionTemplateForm(canonical_post_data, instance=template)
                return _render_registration_page(request, form, template=template, status=400)
            return redirect(
                "book_edition_upload",
                book_code=template.book_code,
                language=utils.normalize_lang(template.language),
            )
        try:
            if legacy_upload_requested or legacy_contract_submission:
                _validate_ingest_v1_request(canonical_post_data, request.FILES)
            _validate_registration_request(canonical_post_data)
        except ValidationError as exc:
            form = BookEditionTemplateForm(canonical_post_data, instance=template)
            messages.error(request, "Corrija os erros do cadastro e tente novamente.")
            for error_message in exc.messages:
                form.add_error(None, error_message)
            return _render_registration_page(request, form, template=template, status=400)

    if request.method == "POST":
        form = BookEditionTemplateForm(canonical_post_data, instance=template)
        if form.is_valid():
            try:
                with transaction.atomic():
                    template = form.save(commit=False)
                    if not template.text_source_mode or template.text_source_mode == "auto":
                        template.text_source_mode = SOURCE_FORMAT_TXT
                    if template.source_saved_path:
                        template.registration_status = BookEditionTemplate.STATUS_READY_FOR_BLOCK_02
                    else:
                        template.registration_status = BookEditionTemplate.STATUS_REGISTERED
                    edition, edition_created = _save_template_and_edition_metadata(template)
            except ValidationError:
                return _render_registration_page(request, form, template=template)
            except Exception as exc:
                logger.exception(
                    "pipeline_registration_failed",
                    extra={
                        "book_code": canonical_post_data.get("book_code"),
                        "language": canonical_post_data.get("language"),
                        "result": "error",
                    },
                )
                form.add_error(
                    None,
                    "Falha ao criar registro editorial automaticamente. "
                    f"Detalhe tecnico: {exc}",
                )
                incoming_file = request.FILES.get("source_file")
                if incoming_file:
                    messages.warning(
                        request,
                        (
                            f"Arquivo recebido ({incoming_file.name}), mas houve erro ao criar a edicao no DB. "
                            "Corrija os erros e tente novamente."
                        ),
                    )
                return _render_registration_page(request, form, template=template)

            if edition_created:
                messages.info(
                    request,
                    f"Edicao editorial criada automaticamente para {template.book_code} [{utils.normalize_lang(template.language)}].",
                )
            if legacy_upload_requested:
                upload_form = BookSourceUploadForm(
                    data={
                        "source_format": canonical_post_data.get("source_format") or template.text_source_mode,
                        "replace_existing": request.POST.get("replace_existing"),
                    },
                    files=request.FILES,
                    has_existing_source=bool(_existing_source_info(template, edition)["path"]),
                    allowed_extensions_getter=_allowed_upload_exts,
                )
                if upload_form.is_valid():
                    return _handle_source_upload(request, template, upload_form, edition=edition)
                messages.error(request, "Corrija os erros do upload e tente novamente.")
                return render(
                    request,
                    "pipeline/book_edition_upload.html",
                    {
                        "template": template,
                        "edition": edition,
                        "upload_form": upload_form,
                        "source_info": _existing_source_info(template, edition),
                        "registration_edit_url": reverse(
                            "book_edition_edit",
                            kwargs={
                                "book_code": template.book_code,
                                "language": utils.normalize_lang(template.language),
                            },
                        ),
                        "steps_url": reverse("edition_steps", kwargs={"edition_id": edition.id}),
                    },
                    status=400,
                )
            messages.success(
                request,
                f"Cadastro salvo para {template.book_code} [{utils.normalize_lang(template.language)}].",
            )
            return redirect(
                "book_edition_upload",
                book_code=template.book_code,
                language=utils.normalize_lang(template.language),
            )
    else:
        if template:
            form = BookEditionTemplateForm(instance=template)
        else:
            form = BookEditionTemplateForm(
                initial={
                    "book_code": initial_book_code,
                    "language": initial_language,
                }
            )

    return _render_registration_page(request, form, template=template)


def book_edition_upload(request, book_code: str, language: str):
    postgres_guard = _require_postgres_ingest_runtime()
    if postgres_guard is not None:
        return postgres_guard
    ensure_bookeditiontemplate_runtime_columns()

    normalized_book_code = (book_code or "").strip()
    normalized_language = utils.normalize_lang((language or "").strip())
    template = (
        BookEditionTemplate.objects.filter(
            book_code=normalized_book_code,
            language=normalized_language,
        ).first()
    )
    if template is None:
        messages.error(request, "Nao e possivel acessar o upload sem cadastro salvo.")
        return redirect("book_edition_new")

    edition = (
        EditorialEdition.objects.select_related("work", "language", "seal")
        .filter(work__code=normalized_book_code, language__code=normalized_language)
        .first()
    )
    if edition is None:
        edition, _ = _save_template_and_edition_metadata(template)

    source_info = _existing_source_info(template, edition)
    forced_source_format = _normalize_source_format(request.GET.get("force_source_format") or "")
    initial_source_format = (
        forced_source_format if forced_source_format in SOURCE_FORMAT_ALLOWED else _source_format_from_template(template)
    )
    if request.method == "POST":
        upload_form = BookSourceUploadForm(
            request.POST,
            request.FILES,
            has_existing_source=bool(source_info["path"]),
            allowed_extensions_getter=_allowed_upload_exts,
            initial={"source_format": initial_source_format},
        )
        if upload_form.is_valid():
            return _handle_source_upload(request, template, upload_form, edition=edition)
        messages.error(request, "Corrija os erros do upload e tente novamente.")
    else:
        upload_form = BookSourceUploadForm(
            initial={"source_format": initial_source_format},
            has_existing_source=bool(source_info["path"]),
            allowed_extensions_getter=_allowed_upload_exts,
        )

    return render(
        request,
        "pipeline/book_edition_upload.html",
        {
            "template": template,
            "edition": edition,
            "upload_form": upload_form,
            "source_info": source_info,
            "registration_edit_url": reverse(
                "book_edition_edit",
                kwargs={"book_code": normalized_book_code, "language": normalized_language},
            ),
            "steps_url": (
                reverse("pipeline_html_dashboard", kwargs={"edition_id": edition.id})
                if initial_source_format == SOURCE_FORMAT_HTML and source_info["path"]
                else reverse("edition_steps", kwargs={"edition_id": edition.id})
            ),
        },
    )


def _parse_book_id(book_code: str) -> int | None:
    try:
        if book_code.startswith("book_"):
            return int(book_code.split("_", 1)[1])
    except (ValueError, IndexError):
        return None

    m = re.search(r"(\d+)", book_code)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _continue_book_options() -> list[dict[str, str]]:
    ensure_bookeditiontemplate_runtime_columns()
    editions = (
        EditorialEdition.objects.select_related("work", "language")
        .order_by("work__code", "language__code")
    )
    pipeline_map = {
        row.edition_id: row
        for row in EditionPipeline.objects.select_related("edition").filter(
            edition_id__in=[edition.id for edition in editions]
        )
    }
    template_map = {
        (row.book_code, utils.normalize_lang(row.language)): row
        for row in BookEditionTemplate.objects.all()
    }

    options: list[dict[str, str]] = []
    for edition in editions:
        book_code = edition.work.code
        parsed = _parse_book_id(book_code)
        if parsed is None:
            continue
        lang = utils.normalize_lang(edition.language.code)
        template = template_map.get((book_code, lang))
        pipeline_state = pipeline_map.get(edition.id)
        current_stage = (pipeline_state.current_stage if pipeline_state else "") or ""
        text_source_mode = _normalize_source_format(
            template.text_source_mode if template else SOURCE_FORMAT_TXT
        )
        if (
            text_source_mode == SOURCE_FORMAT_HTML
            and current_stage
            and _html_stage_rank(current_stage) > 0
        ):
            target_url = reverse("pipeline_html_dashboard", kwargs={"edition_id": edition.id})
        else:
            target_url = reverse("edition_steps", kwargs={"edition_id": edition.id})
        options.append(
            {
                "value": target_url,
                "label": f"{parsed:02d} - {book_code} [{lang}] - {edition.title or edition.work.title}",
            }
        )
    return options


def _frontmatter_template_defaults(edition, language: str) -> dict[str, object]:
    default_year = edition.edition_year or edition.work.year or datetime.now().year
    default_collab = (
        edition.main_contributor.name if edition.main_contributor else edition.work.author.name
    )
    return {
        "title": edition.work.title,
        "subtitle": "",
        "author_name": edition.work.author.name,
        "publication_year": default_year,
        "imprint_name": edition.seal.name,
        "collection_name": "",
        "collaborator_name": default_collab,
        "collaborator_pseudonym": "",
        "collaborator_roles": "",
        "seal_name": edition.seal.name,
        "editor_name": "",
        "translator_name": "",
        "adapter_name": default_collab,
        "language": language,
    }


def _ensure_editorial_templates_for_all_languages(edition) -> list[BookEditionTemplate]:
    templates: list[BookEditionTemplate] = []
    for lang in EDITORIAL_LANGUAGES:
        template, created = BookEditionTemplate.objects.get_or_create(
            book_code=edition.work.code,
            language=lang,
            defaults=_frontmatter_template_defaults(edition, lang),
        )
        if created:
            template.save()
        templates.append(template)
    return templates


def _editorial_required_fields_ready(template: BookEditionTemplate | None) -> bool:
    if template is None:
        return False
    return bool(
        template.frontispiece_rendered.strip()
        and template.copyright_rendered.strip()
        and template.about_edition_rendered.strip()
    )


def _preflight_gate(target_edition, frontmatter_template: BookEditionTemplate | None) -> tuple[bool, str]:
    gate = resolve_preflight_gate(
        editorial_ready=_editorial_required_fields_ready(frontmatter_template),
        merge_refine_clean_path=storage.translated_dir(target_edition.work.code) / "merge_refine_clean.txt",
    )
    return gate.ok, gate.reason


def _editorial_language_rows(templates: list[BookEditionTemplate]) -> list[dict[str, object]]:
    labels = dict(BookEditionTemplate.LANG_CHOICES)
    rows: list[dict[str, object]] = []
    for template in templates:
        warnings = optional_section_warnings(template, utils.normalize_lang(template.language))
        rows.append(
            {
                "code": template.language,
                "label": labels.get(template.language, template.language),
                "required_ready": _editorial_required_fields_ready(template),
                "has_preface": bool(template.has_preface),
                "has_introduction": bool(template.has_introduction),
                "has_epilogue": bool(template.has_epilogue),
                "warnings": warnings,
                "url": reverse(
                    "frontmatter_template_edit",
                    kwargs={"book_code": template.book_code, "language": template.language},
                ),
            }
        )
    return rows


def _block_status_map(*, pipeline_state, raw_path: str | None, frontmatter_template, md_final_exists: bool, build_exists: bool, epub_exists: bool, pdf_exists: bool) -> dict[str, object]:
    block_01_ready = bool(raw_path)
    block_02_done = bool(
        pipeline_state.refined_at
        or pipeline_state.polished_at
        or pipeline_state.merged_at
        or pipeline_state.final_md_at
    )
    return resolve_block_status_map(
        raw_ready=block_01_ready,
        block_02_ready=block_02_done,
        editorial_ready=_editorial_required_fields_ready(frontmatter_template),
        md_final_ready=md_final_exists,
        build_ready=build_exists,
        epub_ready=epub_exists,
        pdf_ready=pdf_exists,
    )


def _next_build_version(edition, language_code: str) -> int:
    latest = (
        EditionBuild.objects.filter(edition=edition, language_code=language_code)
        .order_by("-build_version")
        .first()
    )
    return 1 if latest is None else latest.build_version + 1


def _archive_build_artifact(path: Path, version: int, suffix_label: str) -> str:
    if not path.exists():
        return ""
    archive_dir = path.parent / "history"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{path.stem}.v{version}.{suffix_label}{path.suffix}"
    archive_path.write_bytes(path.read_bytes())
    return str(archive_path)


def _record_build_history(edition, *, language_code: str, build_path: Path | None = None, epub_path: Path | None = None, pdf_path: Path | None = None, notes: str = "") -> EditionBuild:
    if build_path is not None:
        version = _next_build_version(edition, language_code)
        record = EditionBuild.objects.create(
            edition=edition,
            language_code=language_code,
            build_version=version,
            build_type=EditionBuild.BUILD_TYPE_INITIAL if version == 1 else EditionBuild.BUILD_TYPE_REBUILD,
        )
    else:
        latest = (
            EditionBuild.objects.filter(edition=edition, language_code=language_code)
            .order_by("-build_version")
            .first()
        )
        if latest is None:
            version = _next_build_version(edition, language_code)
            record = EditionBuild.objects.create(
                edition=edition,
                language_code=language_code,
                build_version=version,
                build_type=EditionBuild.BUILD_TYPE_INITIAL if version == 1 else EditionBuild.BUILD_TYPE_REBUILD,
            )
        else:
            record = latest
    if build_path is not None:
        record.build_path = _archive_build_artifact(build_path, record.build_version, "build")
    if epub_path is not None:
        record.epub_path = _archive_build_artifact(epub_path, record.build_version, "epub")
    if pdf_path is not None:
        record.pdf_path = _archive_build_artifact(pdf_path, record.build_version, "pdf")
    if notes:
        record.notes = notes
    record.save()
    return record




def _edition_codes(edition) -> tuple[str, str]:
    return edition.work.code, edition.language.code


def _global_core_edition(edition) -> EditorialEdition:
    if utils.normalize_lang(edition.language.code) == "en":
        return edition
    try:
        return EditorialEdition.objects.get(work__code=edition.work.code, language__code="en")
    except EditorialEdition.DoesNotExist as exc:
        raise ValueError(f"Edicao EN nao encontrada para {edition.work.code}.") from exc


def _processing_base_edition(edition) -> EditorialEdition:
    language = utils.normalize_lang(edition.language.code)
    if language == "en":
        return edition

    template = BookEditionTemplate.objects.filter(
        book_code=edition.work.code,
        language=language,
    ).first()
    if template is not None:
        source_mode = _normalize_source_format(getattr(template, "text_source_mode", ""))
        if source_mode in SOURCE_FORMAT_ALLOWED:
            source_saved_path = (getattr(template, "source_saved_path", "") or "").strip()
            if source_saved_path and _resolve_project_path(source_saved_path).exists():
                return edition

    texts = EditionText.objects.filter(edition=edition).first()
    raw_path = ((texts.raw_path if texts else "") or edition.raw_source_path or "").strip()
    if raw_path and _resolve_project_path(raw_path).exists():
        return edition

    source_md = html_preprod.artifact_paths(edition.work.code, language)["md_source"]
    if source_md.exists():
        return edition

    return _global_core_edition(edition)


def _edition_for_language(edition, target_lang: str) -> EditorialEdition:
    normalized = utils.normalize_lang(target_lang)
    if utils.normalize_lang(edition.language.code) == normalized:
        return edition
    try:
        return EditorialEdition.objects.get(work__code=edition.work.code, language__code=normalized)
    except EditorialEdition.DoesNotExist as exc:
        raise ValueError(f"Edicao nao encontrada: {edition.work.code} [{normalized}]") from exc


def _raw_upload_path(edition, uploaded_name: str) -> Path:
    book_code, language = _edition_codes(edition)
    dest_dir = storage.raw_dir(book_code)
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(uploaded_name).suffix or ".txt"
    return storage.raw_source_path(book_code, language, ext)


def _resolve_project_path(path_value: str | Path) -> Path:
    return storage.resolve_storage_path(path_value)


def _normalized_v2_path(book_code: str, language: str) -> Path:
    lang = utils.normalize_lang(language)
    return storage.normalized_path(book_code, lang)


def _split_01_dir(book_code: str) -> Path:
    book_id = _parse_book_id(book_code)
    if book_id is None:
        raise ValueError("book_code must be like book_0001 to resolve split_01 dir.")
    return storage.split_01_dir(f"book_{book_id:04d}")


def _heading_cleaner_dir(book_code: str) -> Path:
    book_id = _parse_book_id(book_code)
    if book_id is None:
        raise ValueError("book_code must be like book_0001 to resolve heading_cleaner dir.")
    return storage.heading_cleaner_dir(f"book_{book_id:04d}")


def _pipeline01_prereq_state(edition) -> dict[str, object]:
    core_edition = _global_core_edition(edition)
    book_code, language = _edition_codes(core_edition)
    language = utils.normalize_lang(language)

    normalized_path = _normalized_v2_path(book_code, language)
    split_dir = _split_01_dir(book_code)
    heading_dir = _heading_cleaner_dir(book_code)
    heading_clean_path = heading_cleaner.clean_path_for_book_code(book_code)
    heading_report_path = heading_cleaner.report_path_for_book_code(book_code)

    split_chunks = sorted(split_dir.glob("*.txt")) if split_dir.exists() else []

    return {
        "book_code": book_code,
        "language": language,
        "normalized_v2_path": normalized_path,
        "normalized_v2_exists": normalized_path.exists(),
        "split_01_dir": split_dir,
        "split_01_count": len(split_chunks),
        "split_01_exists": bool(split_chunks),
        "heading_clean_dir": heading_dir,
        "heading_clean_path": heading_clean_path,
        "heading_clean_report_path": heading_report_path,
        "heading_clean_count": 1 if heading_clean_path.exists() else 0,
        "heading_clean_exists": heading_clean_path.exists(),
        "can_translate": bool(heading_clean_path.exists() and split_chunks),
    }


def _ensure_normalized_v2_for_heading_cleaner(core_edition) -> tuple[Path, str]:
    book_code, language = _edition_codes(core_edition)
    language = utils.normalize_lang(language)
    out_path = _normalized_v2_path(book_code, language)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    texts, _ = EditionText.objects.get_or_create(edition=core_edition)
    source_md_path = html_preprod.artifact_paths(book_code, language)["md_source"]
    if source_md_path.exists():
        source_md_text = source_md_path.read_text(encoding="utf-8")
        normalized_text = pipeline_normalization.normalize_text_v2(source_md_text)
        if source_md_text.endswith("\n") and normalized_text and not normalized_text.endswith("\n"):
            normalized_text += "\n"
        current_text = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
        if current_text != normalized_text:
            out_path.write_text(normalized_text, encoding="utf-8")
        if texts.normalized_text != normalized_text or texts.normalized_path != str(out_path):
            texts.normalized_text = normalized_text
            texts.normalized_path = str(out_path)
            texts.save(update_fields=["normalized_text", "normalized_path", "updated_at"])
        return out_path, "html_source_md"

    if out_path.exists():
        if texts.normalized_path != str(out_path):
            texts.normalized_path = str(out_path)
            texts.save(update_fields=["normalized_path", "updated_at"])
        return out_path, "normalized_v2"

    if texts.normalized_text:
        out_path.write_text(texts.normalized_text, encoding="utf-8")
        texts.normalized_path = str(out_path)
        texts.save(update_fields=["normalized_path", "updated_at"])
        return out_path, "edition_text.normalized_text"

    if texts.normalized_path:
        prev = _resolve_project_path(texts.normalized_path)
        if prev.exists():
            out_path.write_text(prev.read_text(encoding="utf-8"), encoding="utf-8")
            texts.normalized_path = str(out_path)
            texts.save(update_fields=["normalized_path", "updated_at"])
            return out_path, "edition_text.normalized_path"

    raw_path_str = (texts.raw_path or "").strip() or (core_edition.raw_source_path or "").strip()
    if not raw_path_str:
        raise FileNotFoundError("RAW file not found and source.md missing. Cannot prepare normalized_v2.")
    raw_path = _resolve_project_path(raw_path_str)
    if not raw_path.exists():
        raise FileNotFoundError(f"RAW path not found: {raw_path}")
    ext = raw_path.suffix.lstrip(".")
    text = pipeline_ingest.extract_text_from_file(raw_path, ext)
    if not text:
        raise ValueError("Could not extract text from RAW file to prepare normalized_v2.")
    normalized_text = pipeline_normalization.normalize_text_v2(text)
    out_path.write_text(normalized_text, encoding="utf-8")
    texts.raw_text = text
    texts.normalized_text = normalized_text
    texts.raw_path = str(raw_path)
    texts.normalized_path = str(out_path)
    texts.save()
    return out_path, "raw_normalize"


def _invalidate_split_01_after_heading_cleaner(core_edition) -> tuple[Path, int]:
    book_code, _language = _edition_codes(core_edition)
    split_dir = _split_01_dir(book_code)
    existing = sorted(split_dir.glob("*.txt")) if split_dir.exists() else []
    if split_dir.exists():
        shutil.rmtree(split_dir)
    return split_dir, len(existing)


def _invalidate_downstream_pipeline_outputs(core_edition) -> dict[str, int]:
    book_code, _language = _edition_codes(core_edition)
    root = Path(settings.BASE_DIR).parent
    removed = {
        "translated_dirs": 0,
        "translated_files": 0,
        "build_files": 0,
        "edition_core_files": 0,
    }

    book_id = _parse_book_id(book_code)
    if book_id is not None:
        translated_root = root / "data" / "translated" / f"book_{book_id:04d}"
        if translated_root.exists():
            for child in translated_root.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                    removed["translated_dirs"] += 1
                elif child.is_file():
                    child.unlink()
                    removed["translated_files"] += 1

    legacy_merge_clean = root / "data" / "translated" / book_code / "merge_refine_clean.txt"
    if legacy_merge_clean.exists():
        legacy_merge_clean.unlink()
        removed["translated_files"] += 1

    for edition_obj in EditorialEdition.objects.filter(work__code=book_code):
        for build_path in (
            paths.merge_translate_path(edition_obj),
            paths.merge_refine_path(edition_obj),
            paths.preflight_json_path(edition_obj),
            paths.preflight_md_path(edition_obj),
        ):
            if build_path.exists():
                build_path.unlink()
                removed["build_files"] += 1

        edition_core_dir = root / "data" / "editions" / str(edition_obj.id) / "core"
        if edition_core_dir.exists():
            for stale in edition_core_dir.glob("contract_translate_*.json"):
                stale.unlink()
                removed["edition_core_files"] += 1
            for stale in edition_core_dir.glob("contract_refine_*.json"):
                stale.unlink()
                removed["edition_core_files"] += 1
            for stale in edition_core_dir.glob("refine_input_*"):
                if stale.is_dir():
                    shutil.rmtree(stale)
                    removed["edition_core_files"] += 1
                elif stale.exists():
                    stale.unlink()
                    removed["edition_core_files"] += 1

    return removed


def _edition_steps_redirect_url(
    edition,
    *,
    frontmatter_lang: str | None = None,
    frontmatter_locked: bool | None = None,
) -> str:
    book_code, language = _edition_codes(edition)
    template = BookEditionTemplate.objects.filter(
        book_code=book_code,
        language=utils.normalize_lang(language),
    ).first()
    url = reverse("edition_steps", kwargs={"edition_id": edition.id})
    params: dict[str, str] = {}
    if _source_format_from_template(template) == SOURCE_FORMAT_HTML:
        params["allow_html_to_common"] = "1"
    if frontmatter_lang:
        params["frontmatter_lang"] = utils.normalize_lang(frontmatter_lang)
    if frontmatter_locked:
        params["frontmatter_lock"] = "1"
    if params:
        return f"{url}?{urlencode(params)}"
    return url


def _rel_project_path(path: Path) -> str:
    root = Path(settings.BASE_DIR).parent
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _count_non_merged_txt_files(directory: Path | None) -> int:
    if not directory or not directory.exists():
        return 0
    return len(
        [
            p for p in directory.glob("*.txt")
            if not (p.name == "merged.txt" or p.name.startswith("merged_") or p.name.startswith("merge_"))
        ]
    )


def _iter_non_merged_txt_files(directory: Path | None) -> list[Path]:
    if not directory or not directory.exists():
        return []
    return sorted(
        p for p in directory.glob("*.txt")
        if not (p.name == "merged.txt" or p.name.startswith("merged_") or p.name.startswith("merge_"))
    )


def _validate_runtime_chunk_outputs(source_dir: Path | None, candidate_dir: Path | None, stage_label: str) -> None:
    if not source_dir or not candidate_dir or not source_dir.exists() or not candidate_dir.exists():
        return

    issues: list[str] = []
    for candidate_path in _iter_non_merged_txt_files(candidate_dir):
        source_path = source_dir / candidate_path.name
        if not source_path.exists():
            issues.append(f"{candidate_path.name}: source chunk missing in {source_dir}")
            continue

        source_text = source_path.read_text(encoding="utf-8")
        candidate_text = candidate_path.read_text(encoding="utf-8")
        if not candidate_text.strip():
            issues.append(f"{candidate_path.name}: output chunk is empty.")
            continue

        reason = resolve_chunk_truncation_reason(source_text, candidate_text)
        if reason:
            issues.append(f"{candidate_path.name}: {reason}")

    if issues:
        preview = " | ".join(issues[:3])
        extra = f" (+{len(issues) - 3} more)" if len(issues) > 3 else ""
        raise ValueError(
            f"{stage_label} blocked: suspicious chunk ending(s) detected. {preview}{extra}"
        )


def _resolve_refine_merge_candidate(refine_dir: Path | None, target_language: str) -> Path | None:
    if not refine_dir or not refine_dir.exists():
        return None
    candidates = [
        refine_dir / f"merge_refine_{target_language}.txt",
        refine_dir / "merge_refine.txt",
        refine_dir / "merged_return_aldebaran.txt",
        refine_dir / "merged_return_kaiser.txt",
        refine_dir / "merged.txt",
    ]
    candidates.extend(sorted(refine_dir.glob("merged_*.txt")))
    return next((p for p in candidates if p.exists()), None)


def build_pipeline01_steps(edition, pipeline_state: EditionPipeline | None = None) -> list[dict]:
    root = Path(settings.BASE_DIR).parent
    core_edition = _processing_base_edition(edition)
    core_book_code, core_lang = _edition_codes(core_edition)
    core_lang = utils.normalize_lang(core_lang)
    refine_profile = _normalized_refine_profile_for_language(
        (
            getattr(pipeline_state, "refine_profile", "") if pipeline_state is not None else ""
        )
        or _default_refine_profile_for_language(edition.language.code),
        edition.language.code,
    )
    refine_profile_cfg = _refine_profile_config(refine_profile)

    source_md = html_preprod.artifact_paths(core_book_code, core_lang)["md_source"]
    normalized_path = _normalized_v2_path(core_book_code, core_lang)
    split_dir = _split_01_dir(core_book_code)
    split_chunks = sorted(split_dir.glob("*.txt")) if split_dir.exists() else []
    heading_dir = _heading_cleaner_dir(core_book_code)
    heading_clean_path = heading_cleaner.clean_path_for_book_code(core_book_code)
    heading_report_path = heading_cleaner.report_path_for_book_code(core_book_code)

    target_variant = _normalize_translate_variant(
        (pipeline_state.translation_language if pipeline_state and pipeline_state.translation_language else "")
        or edition.language.code
    )
    target_lang = _translate_base_language(target_variant)
    try:
        target_edition = _edition_for_language(edition, target_lang)
    except ValueError:
        target_edition = edition
        target_lang = utils.normalize_lang(edition.language.code)
        target_variant = target_lang

    contract_path: Path | None = None
    contract_exists = False
    contract_error = ""
    try:
        contract_path = _select_contract_path(target_variant)
        contract_exists = contract_path.exists()
    except ValueError as exc:
        contract_error = str(exc)

    translate_dir: Path | None = None
    translate_outputs_count = 0
    translate_merge_path = paths.merge_translate_path(target_edition)
    try:
        translate_dir = _runtime_translate_dir_for_edition(target_edition, target_variant)
        translate_outputs_count = _count_non_merged_txt_files(translate_dir)
    except Exception:
        translate_dir = None
    expected_translate_chunks = len(split_chunks)
    translate_done = bool(
        expected_translate_chunks
        and translate_outputs_count >= expected_translate_chunks
        and translate_merge_path.exists()
    )
    split_by_chapter_dir = paths.split_by_chapter_dir(target_edition)
    split_by_chapter_manifest_path = split_by_chapter_dir / "manifest.json"
    split_by_chapter_done = split_by_chapter_manifest_path.exists()

    refine_source_dir: Path | None = None
    refine_source_label = "translate_chunks"
    try:
        refine_source_dir, refine_source_label = _resolve_refine_source_dir(target_edition, target_variant)
    except Exception:
        refine_source_dir = None
    refine_dir = _resolve_refine_output_dir(
        refine_source_dir,
        refine_profile=refine_profile,
        target_language=target_lang,
    )
    refine_outputs_count = _count_non_merged_txt_files(refine_dir)
    refine_merge_path = paths.merge_refine_path(target_edition)
    refine_runtime_merge = _resolve_refine_merge_candidate(refine_dir, target_lang)
    refine_done = bool(
        translate_done
        and refine_source_dir
        and refine_outputs_count >= _count_non_merged_txt_files(refine_source_dir)
        and (refine_merge_path.exists() or refine_runtime_merge)
    )

    merge_refine_clean_path = root / "data" / "translated" / core_book_code / "merge_refine_clean.txt"
    merge_refine_done = merge_refine_clean_path.exists() and refine_done
    merge_polish_path = paths.merge_polish_path(target_edition)
    polish_done = merge_polish_path.exists()

    texts = EditionText.objects.filter(edition=core_edition).first()
    raw_path_str = ((texts.raw_path if texts else "") or core_edition.raw_source_path or "").strip()
    raw_exists = bool(raw_path_str and _resolve_project_path(raw_path_str).exists())

    step_defs: list[dict] = []

    step_defs.append(
        {
            "n": 1,
            "key": "normalize",
            "title": "Normalize",
            "run_url": reverse("pipeline_normalize_run", kwargs={"edition_id": edition.id}),
            "button_label": "Rodar Normalize",
            "can_run": bool(source_md.exists() or raw_exists),
            "done": normalized_path.exists(),
            "block_reason": (
                "Precisa de source.md (lane HTML) ou RAW de entrada."
                if not (source_md.exists() or raw_exists)
                else ""
            ),
            "outputs": [_rel_project_path(normalized_path)],
            "notes": (
                f"Input HTML: {_rel_project_path(source_md)}"
                if source_md.exists()
                else "Input TXT/RAW detectado."
            ),
        }
    )

    step_defs.append(
        {
            "n": 2,
            "key": "heading_cleaner",
            "title": "HeadingCleaner (Mechanical)",
            "run_url": reverse("pipeline_heading_cleaner_run", kwargs={"edition_id": edition.id}),
            "button_label": "Rodar HeadingCleaner",
            "can_run": normalized_path.exists(),
            "done": heading_clean_path.exists(),
            "block_reason": "Prerequisito: normalized_v2.txt." if not normalized_path.exists() else "",
            "outputs": [
                _rel_project_path(heading_clean_path),
                _rel_project_path(heading_report_path),
            ],
            "notes": "Mechanical cleaner: remove TOC, divisorias e wrappers antes de refazer split_01.",
        }
    )

    step_defs.append(
        {
            "n": 3,
            "key": "chunk",
            "title": "Split/Chunk",
            "run_url": reverse("pipeline_chunk_run", kwargs={"edition_id": edition.id}),
            "button_label": "Rodar Split/Chunk",
            "can_run": heading_clean_path.exists(),
            "done": bool(split_chunks),
            "block_reason": "Prerequisito: heading_cleaner/clean.txt." if not heading_clean_path.exists() else "",
            "outputs": [_rel_project_path(split_dir / "*.txt")],
            "notes": f"Chunks detectados: {len(split_chunks)} | fonte: {_rel_project_path(heading_clean_path)}",
        }
    )

    step_defs.append(
        {
            "n": 4,
            "key": "translate",
            "title": "Translate (script + JSON)",
            "run_url": reverse("pipeline_translate_run", kwargs={"edition_id": edition.id}),
            "button_label": "Rodar Translate",
            "can_run": bool(heading_clean_path.exists() and split_chunks) and contract_exists,
            "done": translate_done,
            "block_reason": (
                "Prerequisito: rode HeadingCleaner e depois refaca split_01."
                if not heading_clean_path.exists() or not split_chunks
                else ("Contrato JSON nao encontrado." if not contract_exists else "")
            ),
            "outputs": [
                _rel_project_path(translate_dir / "*.txt") if translate_dir else "data/translated/<book>/<lang_variant>/*.txt",
                _rel_project_path(translate_merge_path),
            ],
            "notes": (
                f"Translate contract: {_rel_project_path(contract_path)} | profile={_translate_variant_label(target_variant)} | chunks={translate_outputs_count}/{expected_translate_chunks}"
                if contract_path
                else f"Translate contract: {contract_error or 'nao resolvido'}"
            ),
        }
    )

    step_defs.append(
        {
            "n": 5,
            "key": "split_by_chapter",
            "title": "Split by Chapter (merge_translate)",
            "run_url": reverse("pipeline_run_edition_step", kwargs={"edition_id": edition.id, "step": "split_by_chapter"}),
            "button_label": "Rodar Split by Chapter",
            "can_run": translate_done and translate_merge_path.exists(),
            "done": split_by_chapter_done,
            "block_reason": (
                "Prerequisito: merge_translate.txt canonico no build."
                if not (translate_done and translate_merge_path.exists())
                else ""
            ),
            "outputs": [
                _rel_project_path(split_by_chapter_dir / "parts" / "*.txt"),
                _rel_project_path(split_by_chapter_manifest_path),
            ],
            "notes": "Usa merge_translate.txt, detecta capitulos e salva 1 arquivo por capitulo por padrao. So divide em 2 quando solicitado. Nao envia nada para OpenAI.",
        }
    )

    step_defs.append(
        {
            "n": 6,
            "key": "refine",
            "title": f"Refine ({refine_profile_cfg['label']})",
            "run_url": reverse("pipeline_refine_run", kwargs={"edition_id": edition.id}),
            "button_label": "Rodar Refine",
            "can_run": translate_done,
            "done": refine_done,
            "block_reason": (
                "Prerequisito: translate completo com merge correspondente."
                if not translate_done
                else ""
            ),
            "outputs": [
                _rel_project_path(refine_dir / "*.txt") if refine_dir else f"data/translated/<book>/<lang_variant>/{REFINE_RETURN_DIRNAME}/*.txt",
                _rel_project_path(refine_merge_path),
            ],
            "notes": (
                f"Perfil: {refine_profile_cfg['label']} | Agent: {refine_profile_cfg['agent_name']} "
                f"| source={refine_source_label} | chunks={refine_outputs_count}/{_count_non_merged_txt_files(refine_source_dir)}"
            ),
        }
    )

    step_defs.append(
        {
            "n": 7,
            "key": "merge_refine",
            "title": "Merge/Finalize",
            "run_url": reverse("pipeline_merge_refine_run", kwargs={"edition_id": edition.id}),
            "button_label": "Rodar MergeRefine",
            "can_run": refine_done,
            "done": merge_refine_done,
            "block_reason": "Prerequisito: refine completo com merge correspondente." if not refine_done else "",
            "outputs": [
                _rel_project_path(refine_merge_path),
                _rel_project_path(merge_refine_clean_path),
            ],
            "notes": "Gera merge_refine_clean.txt canônico do Pipeline 01.",
        }
    )

    if getattr(target_edition, "lock_polish", False) or polish_done:
        step_defs.append(
            {
                "n": 8,
                "key": "polish_return",
                "title": "Polish Return (Optional)",
                "run_url": reverse("pipeline_run_edition_step", kwargs={"edition_id": edition.id, "step": "polish_return"}),
                "button_label": "Rodar Polish Return",
                "can_run": merge_refine_done,
                "done": polish_done,
                "block_reason": "Prerequisito: merge_refine_clean.txt canônico." if not merge_refine_done else "",
                "outputs": [
                    _rel_project_path(paths.merge_polish_path(target_edition)),
                ],
                "notes": "Etapa opcional. Quando ativada em Lock polish, roda logo após o Merge Refine e antes do Pre-flight, gerando merge_polish.txt.",
            }
        )

    return step_defs


def _select_contract_path(language: str) -> Path:
    mapping = {
        "en": "gaiden/contracts/en_modern_2025.json",
        "en_philo": "gaiden/contracts/en_philosofer_2026.json",
        "en_devotional": "gaiden/contracts/en_devotional_2026.json",
        "es": "gaiden/contracts/en_modern_2025.json",
        "ptbr": "gaiden/contracts/en_modern_2025.json",
        "de": "gaiden/contracts/en_de_2026.json",
        "fr": "gaiden/contracts/en_fr_2025.json",
        "it": "gaiden/contracts/en_it_2025.json",
    }
    rel = mapping.get(_normalize_translate_variant(language))
    if not rel:
        raise ValueError(f"No translate contract for language={language}")
    preferred = storage.repo_contract_path(rel)
    if preferred.exists():
        return preferred
    return storage.repo_contract_path(rel)


def _select_refine_contract(language: str, refine_profile: str | None = None) -> Path:
    mapping = {
        "en": "gaiden/contracts/refine/en_refine_2025.json",
        "es": "gaiden/contracts/refine/es_refine_2025.json",
        "ptbr": "gaiden/contracts/refine/ptbr_refine_2025.json",
        "de": "gaiden/contracts/refine/de_refine_2026.json",
        "it": "gaiden/contracts/refine/it_refine_2025.json",
    }
    rel = mapping.get(_translate_base_language(language))
    if not rel:
        raise ValueError(f"No refine contract for language={language}")
    preferred = storage.repo_contract_path(rel)
    if preferred.exists():
        return preferred
    return storage.repo_contract_path(rel)


def _select_polish_contract(language: str) -> Path:
    mapping = {
        "en": "gaiden/contracts/polish/en_polish_2025.json",
    }
    normalized = _normalize_translate_variant(language)
    rel = mapping.get(normalized) or mapping.get(_translate_base_language(language))
    if not rel:
        raise ValueError(f"No polish contract for language={language}")
    preferred = storage.repo_contract_path(rel)
    if preferred.exists():
        return preferred
    return storage.repo_contract_path(rel)


def _contract_target_lang(payload: dict) -> str:
    candidates = [
        ("target_language",),
        ("target_lang",),
        ("output_language",),
        ("output", "language"),
        ("output", "lang"),
        ("language",),
    ]
    for path in candidates:
        val = payload
        ok = True
        for key in path:
            if isinstance(val, dict) and key in val:
                val = val[key]
            else:
                ok = False
                break
        if ok and isinstance(val, str) and val.strip():
            return val.strip()
    return "en"


def _resolve_contract_out_dir(contract_path: Path, edition) -> Path:
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    out_dir = payload.get("out_dir")
    if out_dir:
        out_dir_path = Path(out_dir)
        if out_dir_path.is_absolute():
            return out_dir_path
        return Path(settings.BASE_DIR).parent / out_dir_path
    book_id = _parse_book_id(_edition_codes(edition)[0])
    if book_id is None:
        raise ValueError("book_code must be like book_0001 to resolve out_dir.")
    target_lang = _contract_target_lang(payload)
    return Path("data/translated") / f"book_{book_id:04d}" / "split_01" / target_lang


def _translate_source_chunks(book_code: str) -> tuple[Path, str, str]:
    """
    Translate reads only the rechunked split_01 generated after HeadingCleaner.
    Returns (chunk_dir, input_glob, source_label).
    """
    book_id = _parse_book_id(book_code)
    if book_id is None:
        raise ValueError("book_code must be like book_0001 to resolve translate chunks.")

    project_root = Path(settings.BASE_DIR).parent
    split_dir = project_root / "data" / "chunks" / f"book_{book_id:04d}" / "split_01"
    return split_dir, "*.txt", "split_01"


def _runtime_translate_out_dir(book_code: str, target_language: str, payload: dict) -> Path:
    book_id = _parse_book_id(book_code)
    if book_id is None:
        raise ValueError("book_code must be like book_0001 to resolve translate out_dir.")
    book_token = f"book_{book_id:04d}"
    variant_key = _normalize_translate_variant(target_language)
    base_lang = _translate_base_language(variant_key)

    out_dir = payload.get("out_dir")
    if isinstance(out_dir, str) and out_dir.strip():
        out_path = Path(out_dir.strip())
        parts = list(out_path.parts)
        for idx, part in enumerate(parts):
            if re.fullmatch(r"book_\d{4}", part):
                parts[idx] = book_token
                return Path(*parts)

    io = payload.get("io") if isinstance(payload.get("io"), dict) else {}
    variant = (
        io.get("lang_variant")
        if isinstance(io.get("lang_variant"), str) and io.get("lang_variant").strip()
        else ("en_philosofer_2026" if variant_key == "en_philo" else f"{base_lang}_2025")
        if variant_key != "en_devotional"
        else "en_devotional_2026"
    )
    return storage.translated_dir(book_token, str(variant)).relative_to(storage.repo_root())


def _append_prompt_block(prompt: str, block: str) -> str:
    prompt = (prompt or "").strip()
    block = block.strip()
    if not block:
        return prompt
    if block in prompt:
        return prompt
    if not prompt:
        return block
    return f"{prompt}\n\n{block}"


def _english_controlled_translation_defaults() -> dict:
    return {
        "name": "Controlled Modern English Translation (2026)",
        "contract_name": "stage01_modern_translation_controlled_v3",
        "status": "active",
        "supersedes": ["stage01_modern_translation_controlled_v2"],
        "role": "translator_modernizer",
        "purpose": (
            "Produce a controlled modern English version of the source text while strictly preserving meaning, "
            "narrative continuity, paragraph structure, and literary intent."
        ),
        "stage": "translate",
        "model": "gpt-5.4",
        "model_lock": True,
        "fallback_model": "gpt-5.2",
        "fallback_policy": "on_no_response",
        "temperature": 0.2,
        "max_output_tokens": 1800,
        "chunk_dir": "data/chunks/book_0001/split_01",
        "out_dir": "data/translated/book_0001/en_modern_2026",
        "output": {"language": "en"},
        "contract_policy": {
            "mode": "controlled_modernization",
            "priority_order": [
                "semantic_fidelity",
                "narrative_continuity",
                "paragraph_integrity",
                "literary_register_preservation",
                "conservative_modernization",
                "fluency_and_readability",
            ],
        },
        "core_objectives": [
            "Preserve the full meaning of the source text.",
            "Preserve chronology, causality, paragraph order, and narrative flow.",
            "Modernize archaic English only where genuine readability friction exists.",
            "Maintain natural, fluent, literate modern English.",
            "Preserve tone, atmosphere, and character voice distinctions.",
        ],
        "editorial_rules": [
            "Do not summarize, compress, explain, annotate, embellish, expand, or invent content.",
            "Do not alter names, places, dates, numbers, or narrative facts.",
            "Do not change narrative perspective.",
            "Do not neutralize genre-specific atmosphere.",
            "Do not flatten elevated, ceremonial, mystical, or intentionally literary diction when it remains readable.",
            "Prefer conservative edits over aggressive rewriting.",
            "Preserve recurring lexical choices when they are clearly intentional.",
        ],
        "modernization_rules": [
            "Replace archaic words and constructions with modern equivalents only when meaning is unchanged.",
            "Modernize obsolete auxiliaries and connectors when readability improves and tone is preserved.",
            "Remove unnecessary archaism, but do not sterilize the prose.",
            "Keep author-specific narrative weight, tension, and atmosphere intact.",
            "Preserve intentional literary register whenever it remains readable to a contemporary audience.",
        ],
        "structure_rules": [
            "Preserve paragraph boundaries.",
            "Preserve heading and chapter markers if they exist.",
            "Preserve dialogue structure and speaker distinctions.",
            "Do not reorder events, sentences, or paragraphs.",
            "Do not convert narration into exposition or summary.",
            "Do not collapse descriptive buildup into shorter generic phrasing.",
        ],
        "sentence_handling": {
            "allow_sentence_splitting": True,
            "conditions": [
                "Split only when the original sentence is excessively long and readability clearly improves.",
                "Preserve full meaning, tone, and logical relation when splitting.",
                "Ensure each resulting sentence is grammatically complete.",
            ],
            "forbidden": [
                "Dropping subjects during splitting.",
                "Creating fragments from complete source sentences.",
                "Flattening complex syntax unnecessarily.",
                "Using splitting as a way to simplify away nuance.",
            ],
        },
        "dialogue_rules": [
            "Preserve character voice distinctions.",
            "Preserve dialogue meaning exactly.",
            "Modernize archaic dialogue only where real friction exists.",
            "Do not make dialogue sound slangy, casual, or contemporary beyond the contract.",
            "Preserve intentional brevity, tension, and subtext in spoken exchanges.",
        ],
        "continuity_rules": [
            "Maintain continuity with adjacent chunks.",
            "Do not introduce contradictions with previous or subsequent text.",
            "Preserve recurring terms, motifs, and narrative references whenever clearly intentional.",
            "When local ambiguity exists, prefer interpretations that preserve continuity rather than simplify.",
        ],
        "style_constraints": [
            "Use natural, fluent, literate modern English.",
            "Avoid pseudo-archaic language.",
            "Avoid generic filler phrasing.",
            "Avoid modern slang.",
            "Avoid over-smoothing distinctive prose.",
            "Do not rewrite the passage into a different stylistic register.",
        ],
        "forbidden_behaviors": [
            "Summarizing content.",
            "Omitting narrative details.",
            "Expanding content beyond the source.",
            "Injecting commentary or explanation.",
            "Changing chronology or causality.",
            "Changing paragraph structure.",
            "Changing narrative voice.",
            "Replacing specific imagery with generic modern wording.",
            "Over-modernizing intentionally literary prose.",
        ],
        "qa_requirements": [
            "Reject output if meaning is reduced.",
            "Reject output if any paragraph functions like a summary of the source paragraph.",
            "Reject output if tone or atmosphere is substantially flattened.",
            "Reject output if paragraph boundaries are altered without necessity.",
            "Reject output if character voice distinctions are lost.",
            "Reject output if continuity with adjacent chunks is broken.",
            "Reject output if names, places, dates, or numbers are altered.",
            "Reject output if the text becomes generically modern instead of controlled modern literary English.",
        ],
        "output_standard": [
            "The result must read as natural, fluent, literate modern English.",
            "The result must preserve full meaning, narrative flow, and literary intent.",
            "The result must remain structurally faithful to the source.",
            "The result must not feel summarized, flattened, slangy, or artificially rewritten.",
        ],
        "system_prompt": (
            "You are a literary translator-editor. Rewrite the passage into controlled modern English while preserving "
            "meaning, chronology, paragraph structure, tone, atmosphere, narrative continuity, and literary intent. "
            "Output only the rewritten passage. Do not summarize, explain, annotate, add headings, add notes, or "
            "mention the task. Preserve dialogue, names, places, dates, numbers, and chapter markers exactly unless "
            "only surface-level linguistic modernization is required."
        ),
        "user_prompt": (
            "Rewrite the following literary passage into controlled modern English.\n\n"
            "Requirements:\n"
            "- Preserve meaning, chronology, paragraph structure, dialogue, names, places, dates, and numbers.\n"
            "- Do not summarize, compress, expand, explain, annotate, or invent content.\n"
            "- Modernize archaic wording only where it creates real readability friction.\n"
            "- Preserve tone, narrative voice, literary register, and genre atmosphere.\n"
            "- Keep complete grammatical sentences and clauses unless the source intentionally uses fragmentary dialogue effects worth preserving.\n"
            "- Split sentences only if readability clearly improves and no meaning, tone, or nuance is lost.\n"
            "- Keep headings or chapter markers if present.\n"
            "- Preserve continuity with adjacent chunks.\n\n"
            "Return only the final rewritten passage.\n\n"
            "{text}"
        ),
    }


def _merge_translate_contract_defaults(payload: dict) -> dict:
    merged = dict(payload)
    for key, value in _english_controlled_translation_defaults().items():
        merged.setdefault(key, value)
    return merged


def _english_translate_contract_block(payload: dict) -> str:
    payload = _merge_translate_contract_defaults(payload)
    sentence_handling = payload.get("sentence_handling") if isinstance(payload.get("sentence_handling"), dict) else {}

    sections: list[str] = []
    contract_policy = payload.get("contract_policy") if isinstance(payload.get("contract_policy"), dict) else {}
    if contract_policy:
        policy_lines = []
        if contract_policy.get("mode"):
            policy_lines.append(f"- Mode: {contract_policy['mode']}")
        priority_order = contract_policy.get("priority_order")
        if isinstance(priority_order, list):
            policy_lines.extend(f"- {item}" for item in priority_order if str(item).strip())
        if policy_lines:
            sections.append("CONTRACT POLICY:\n" + "\n".join(policy_lines))

    for title, key in [
        ("CORE OBJECTIVES", "core_objectives"),
        ("EDITORIAL RULES", "editorial_rules"),
        ("MODERNIZATION RULES", "modernization_rules"),
        ("STRUCTURE RULES", "structure_rules"),
        ("DIALOGUE RULES", "dialogue_rules"),
        ("CONTINUITY RULES", "continuity_rules"),
        ("STYLE CONSTRAINTS", "style_constraints"),
        ("FORBIDDEN BEHAVIORS", "forbidden_behaviors"),
        ("QA REQUIREMENTS", "qa_requirements"),
        ("OUTPUT STANDARD", "output_standard"),
    ]:
        items = payload.get(key)
        if isinstance(items, list) and items:
            sections.append(title + ":\n" + "\n".join(f"- {item}" for item in items if str(item).strip()))

    if sentence_handling:
        sentence_lines = []
        if sentence_handling.get("allow_sentence_splitting"):
            sentence_lines.append("- Sentence splitting is allowed only under the contract conditions below.")
        conditions = sentence_handling.get("conditions")
        if isinstance(conditions, list):
            sentence_lines.extend(f"- {item}" for item in conditions if str(item).strip())
        forbidden = sentence_handling.get("forbidden")
        if isinstance(forbidden, list) and forbidden:
            sentence_lines.append("FORBIDDEN DURING SPLITTING:")
            sentence_lines.extend(f"- {item}" for item in forbidden if str(item).strip())
        if sentence_lines:
            sections.append("SENTENCE HANDLING:\n" + "\n".join(sentence_lines))

    purpose = str(payload.get("purpose") or "").strip()
    if purpose:
        sections.insert(0, f"PURPOSE:\n- {purpose}")

    return "\n\n".join(sections)


def _generic_translate_prompts(target_language: str, payload: dict | None = None) -> tuple[str, str, str]:
    payload = payload or {}
    variant = _normalize_translate_variant(target_language)
    lang = _translate_base_language(variant)
    target_labels = {
        "en": "modern, natural English",
        "en_philo": "modern philosophical English",
        "en_devotional": "modern devotional English",
        "es": "modern, natural Spanish",
        "ptbr": "modern Brazilian Portuguese",
        "de": "modern, natural German",
        "fr": "modern, natural French",
        "it": "modern, natural Italian",
    }
    target_label = target_labels.get(variant, target_labels.get(lang, f"modern {lang}"))

    if lang == "en":
        payload = _merge_translate_contract_defaults(payload)
        system_prompt = str(payload.get("system_prompt") or "").strip()
        user_prompt = str(payload.get("user_prompt") or "").strip()
        if not system_prompt or not user_prompt:
            english_contract_block = _english_translate_contract_block(payload)
            if variant == "en_philo":
                system_prompt = (
                    "You are a literary translator-editor working on philosophical prose. Rewrite the passage into controlled "
                    "philosophical English while preserving meaning, chronology, paragraph structure, tone, atmosphere, "
                    "narrative continuity, and literary intent. Translate embedded Greek and Latin expressions into natural "
                    "English whenever they function as source text. Output only the rewritten passage."
                )
                user_prompt = (
                    "Rewrite the following literary-philosophical passage into controlled philosophical English.\n\n"
                    f"{english_contract_block}\n\n"
                    "Translate embedded Greek and Latin words, phrases, and quotations into natural English when they function as readable source text.\n\n"
                    "Return only the final rewritten passage.\n\n"
                    "{text}"
                )
            elif variant == "en_devotional":
                system_prompt = (
                    "You are a literary translator-editor working on devotional and ascetic prose. Rewrite the passage into "
                    "controlled modern devotional English while preserving meaning, chronology, paragraph structure, "
                    "numbering, tone, spiritual gravity, and literary intent. Output only the rewritten passage."
                )
                user_prompt = (
                    "Rewrite the following devotional passage into controlled modern devotional English.\n\n"
                    f"{english_contract_block}\n\n"
                    "Preserve all numbered sections, prayers, responses, and citations in the same order. "
                    "Do not omit closing lines just because the opening feels complete.\n\n"
                    "Return only the final rewritten passage.\n\n"
                    "{text}"
                )
            else:
                system_prompt = (
                    "You are a literary translator-editor. Rewrite the passage into controlled modern English while preserving "
                    "meaning, chronology, paragraph structure, tone, atmosphere, narrative continuity, and literary intent. "
                    "Output only the rewritten passage."
                )
                user_prompt = (
                    "Rewrite the following literary passage into controlled modern English.\n\n"
                    f"{english_contract_block}\n\n"
                    "Return only the final rewritten passage.\n\n"
                    "{text}"
                )
        return system_prompt, user_prompt, target_label
    english_payload = _merge_translate_contract_defaults(payload)
    english_system = str(english_payload.get("system_prompt") or "").strip()
    english_user = str(english_payload.get("user_prompt") or "").strip()
    if not english_system or not english_user:
        english_contract_block = _english_translate_contract_block(english_payload)
        english_system = (
            "You are a literary translator-editor. Translate the passage into the requested target language while preserving "
            "meaning, chronology, paragraph structure, tone, atmosphere, narrative continuity, and literary intent. "
            "Output only the translated passage."
        )
        english_user = (
            "Translate the following literary passage into the requested target language.\n\n"
            f"{english_contract_block}\n\n"
            "Return only the final translated passage.\n\n"
            "{text}"
        )

    system_prompt = (
        f"You are a literary translator-editor working into {target_label}. "
        "Preserve meaning, chronology, paragraph structure, tone, atmosphere, narrative continuity, and literary intent. "
        "Output only the translated passage."
    )
    user_prompt = (
        english_user
        .replace("controlled modern English", target_label)
        .replace("modern English", target_label)
        .replace("rewritten passage", "translated passage")
        .replace("Rewrite the following literary passage into", "Translate the following literary passage into")
        .replace("Rewrite the passage into", "Translate the passage into")
    )
    return system_prompt, user_prompt, target_label


def _harden_translate_contract(payload: dict, target_language: str) -> dict:
    if _translate_base_language(target_language) == "en":
        payload = _merge_translate_contract_defaults(payload)
    system_prompt, user_prompt, target_label = _generic_translate_prompts(target_language, payload)
    payload["name"] = f"Pipeline runtime literary translate -> {target_label}"
    if str(payload.get("model") or "").strip() in {"", "gpt-5.1", "gpt-5-chat-latest"}:
        payload["model"] = "gpt-5.4"
    payload["system_prompt"] = system_prompt
    payload["user_prompt"] = user_prompt
    return payload


def _recommended_translate_max_output_tokens(
    chunk_dir: Path,
    input_glob: str,
    target_language: str,
    current_limit: int | None = None,
) -> int:
    txt_files = sorted(chunk_dir.glob(input_glob or "*.txt"))
    if not txt_files:
        return max(int(current_limit or 0), 1800)

    max_chars = 0
    for path in txt_files:
        try:
            char_count = len(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if char_count > max_chars:
            max_chars = char_count

    if max_chars <= 0:
        return max(int(current_limit or 0), 1800)

    normalized_lang = _translate_base_language(target_language)
    chars_per_token = 4.0 if normalized_lang == "en" else 3.6
    estimated_tokens = int(max_chars / chars_per_token)
    recommended = max(1800, int(estimated_tokens * 1.45) + 320)
    # Large Holmes/Lovecraft chunks can legitimately exceed 6k output tokens
    # when we preserve paragraph structure and avoid summarization.
    recommended = min(recommended, 12000)
    return max(int(current_limit or 0), recommended)


def _english_refine_micro_polish_block(refine_profile_cfg: dict[str, str]) -> str:
    return (
        "ENGLISH REFINE MODE: SURGICAL MICRO-POLISH ONLY.\n"
        "- Make only surgical edits where real reading friction exists.\n"
        "- If a sentence is already clear, atmospheric, and readable, leave it unchanged.\n"
        "- Preserve the source author's voice, cadence, and genre-specific atmosphere.\n"
        f"- Preserve the active profile target: {refine_profile_cfg['label']} via {refine_profile_cfg['agent_name']}.\n"
        "- Do not flatten elevated narration into generic modern prose.\n"
        "- Preserve ceremonial, royal, priestly, mystical, and epic diction when it sounds intentional and strong.\n"
        "- Prefer lighter edits in dialogue and stronger edits in stiff exposition.\n"
        "- Preserve character voice distinctions; keep forceful characters forceful and restrained characters restrained.\n"
        "- Reduce over-formal or bureaucratic phrasing only when it feels heavier than the surrounding prose.\n"
        "- Replace heavy diction only when a simpler alternative preserves tone and force.\n"
        "- Split long sentences only when the split improves momentum or clarity without killing rhythm.\n"
        "- Do not globally modernize the book. Refine only the lines that truly need help."
    )


def _harden_refine_contract(
    payload: dict,
    refine_profile: str | None = None,
    target_language: str | None = None,
) -> dict:
    refine_profile = _normalize_refine_profile(refine_profile)
    refine_profile_cfg = _refine_profile_config(refine_profile)
    normalized_lang = utils.normalize_lang(
        target_language
        or str(payload.get("target_language") or payload.get("language") or "")
    )
    instructions = payload.get("instructions") if isinstance(payload.get("instructions"), dict) else {}
    output = instructions.get("output") if isinstance(instructions.get("output"), dict) else {}

    goal = str(instructions.get("goal") or "").strip()
    system_parts = [
        "You are a professional literary refine agent processing already-translated book text.",
        f"Active refine profile: {refine_profile_cfg['label']} via agent {refine_profile_cfg['agent_name']}.",
        refine_profile_cfg["style_directive"],
        "Function: receive the existing chunk, refine it in the same target language, and return only the refined chunk.",
        "This is a surgical pass, not a rewrite pass.",
        "Keep the chunk in the same language as the input. Never leak another language into the output.",
        "Preserve every paragraph boundary, heading, speaker turn, factual detail, chronology, and named entity.",
        "Make only local improvements for fluency, clarity, cadence, punctuation, and naturalness where truly needed.",
        "Do not globally rewrite the passage. Do not change register beyond what the active profile explicitly requires.",
    ]
    if goal:
        system_parts.append(goal)

    output_rules = [
        "CRITICAL OUTPUT RULES:",
        "- Output only the refined passage.",
        "- Preserve all information, chronology, speakers, dialogue, paragraph structure, and any existing chapter or section headings.",
        "- Do not summarize, compress, paraphrase away details, skip any sentence, or invent connective material.",
        "- Do not delete, rename, or renumber existing headings or chapter markers.",
        "- Do not add new titles, headings, notes, labels, commentary, explanations, or metadata.",
        "- Do not translate the chunk into another language.",
        "- Do not mention the prompt, the source text, or your editing choices.",
        "- Return only the refined passage as continuous prose and dialogue.",
    ]
    if output.get("no_notes"):
        output_rules.append("- No notes before or after the passage.")
    if output.get("no_disclaimers"):
        output_rules.append("- No disclaimers.")
    if output.get("no_metadata"):
        output_rules.append("- No metadata.")

    user_prompt = (
        "Refine the following already-translated passage in the same language in which it is written.\n\n"
        f"Selected profile: {refine_profile_cfg['label']}.\n"
        "This is a surgical editorial pass.\n"
        "Keep the same language as the input.\n"
        "Preserve every fact, sentence-level meaning, dialogue turn, paragraph boundary, and any chapter/section heading already present.\n"
        "Return only the refined passage.\n"
        "Do not summarize.\n"
        "Do not omit any sentence or paragraph.\n"
        "Do not delete or rewrite headings.\n"
        "Do not translate into another language.\n"
        "Do not add commentary.\n\n"
        "{text}"
    )

    payload["name"] = f"Runtime refine {refine_profile_cfg['label']} -> {refine_profile_cfg['agent_name']}"
    payload["refine_profile"] = refine_profile
    payload["agent_name"] = refine_profile_cfg["agent_name"]
    payload["system_prompt"] = _append_prompt_block(
        payload.get("system_prompt") or payload.get("system") or "",
        "\n\n".join(system_parts + ["\n".join(output_rules)]),
    )
    payload["user_prompt"] = _append_prompt_block(
        payload.get("user_prompt") or payload.get("user") or "",
        user_prompt,
    )
    return payload


def _build_runtime_translate_contract(edition, target_language: str) -> tuple[Path, str]:
    book_code, _language = _edition_codes(edition)
    target_variant = _normalize_translate_variant(target_language)
    target_base = _translate_base_language(target_variant)
    base_contract_path = _select_contract_path(target_variant)
    payload = json.loads(base_contract_path.read_text(encoding="utf-8"))
    payload = _harden_translate_contract(payload, target_variant)

    chunk_dir, input_glob, source_label = _translate_source_chunks(book_code)
    out_dir = _runtime_translate_out_dir(book_code, target_variant, payload)
    if not out_dir.is_absolute():
        out_dir = Path(settings.BASE_DIR).parent / out_dir

    payload["chunk_dir"] = str(chunk_dir)
    payload["input_glob"] = input_glob
    payload["out_dir"] = str(out_dir)
    payload["target_language"] = target_base
    payload["translation_variant"] = target_variant
    payload["max_output_tokens"] = _recommended_translate_max_output_tokens(
        chunk_dir,
        input_glob,
        target_base,
        current_limit=payload.get("max_output_tokens"),
    )

    if not isinstance(payload.get("output"), dict):
        payload["output"] = {}
    if target_base == "ptbr":
        payload["output"]["language"] = "pt-br"
    else:
        payload["output"]["language"] = target_base

    runtime_contract_path = (
        Path(settings.BASE_DIR).parent
        / "data"
        / "editions"
        / str(edition.id)
        / "core"
        / f"contract_translate_{target_variant}.json"
    )
    runtime_contract_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_contract_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return runtime_contract_path, source_label


def _runtime_translate_dir_for_edition(edition, target_language: str) -> Path:
    book_code, _language = _edition_codes(edition)
    target_variant = _normalize_translate_variant(target_language)
    payload = json.loads(_select_contract_path(target_variant).read_text(encoding="utf-8"))
    out_dir = _runtime_translate_out_dir(book_code, target_variant, payload)
    expected_dir = out_dir if out_dir.is_absolute() else Path(settings.BASE_DIR).parent / out_dir
    if expected_dir.exists():
        return expected_dir

    for candidate in paths.translated_variant_dirs(book_code, _translate_base_language(target_variant)):
        if _count_non_merged_txt_files(candidate):
            return candidate

    return expected_dir


def _resolve_refine_source_dir(edition, target_language: str) -> tuple[Path, str]:
    split_root = paths.split_by_chapter_dir(edition)
    split_manifest = split_root / "manifest.json"
    split_parts_dir = split_root / "parts"
    if split_manifest.exists() and split_parts_dir.exists():
        return split_parts_dir, "split_by_chapter/parts"
    return _runtime_translate_dir_for_edition(edition, target_language), "translate_chunks"


def _build_runtime_refine_contract(
    edition,
    target_language: str,
    refine_profile: str | None = None,
) -> tuple[Path, Path, Path]:
    target_base = _translate_base_language(target_language)
    payload = json.loads(_select_refine_contract(target_base, refine_profile=refine_profile).read_text(encoding="utf-8"))
    payload = _harden_refine_contract(
        payload,
        refine_profile=refine_profile,
        target_language=target_base,
    )
    source_dir, _source_label = _resolve_refine_source_dir(edition, target_language)
    if not source_dir.exists():
        raise FileNotFoundError(f"Refine source chunks not found: {source_dir}. Run Translate or Split by Chapter first.")

    refine_input_dir = (
        Path(settings.BASE_DIR).parent
        / "data"
        / "editions"
        / str(edition.id)
        / "core"
        / f"refine_input_{target_base}"
    )
    refine_input_dir.mkdir(parents=True, exist_ok=True)
    for stale in refine_input_dir.glob("*.txt"):
        stale.unlink()

    source_chunks = [
        p for p in sorted(source_dir.glob("*.txt"))
        if not (p.name == "merged.txt" or p.name.startswith("merged_"))
    ]
    if not source_chunks:
        raise FileNotFoundError(f"No translate chunks found in {source_dir} for refine input.")
    for path in source_chunks:
        shutil.copyfile(path, refine_input_dir / path.name)

    out_dir = _resolve_refine_output_dir(
        source_dir,
        refine_profile=refine_profile,
        target_language=target_base,
    )
    if out_dir is None:
        raise FileNotFoundError("Unable to resolve refine output directory.")
    payload["chunk_dir"] = str(refine_input_dir)
    payload["out_dir"] = str(out_dir)
    payload["target_language"] = target_base
    payload["max_output_tokens"] = min(
        _recommended_translate_max_output_tokens(
        refine_input_dir,
        "*.txt",
        target_base,
        current_limit=payload.get("max_output_tokens"),
        ),
        4000,
    )
    payload["sanitize_failure_fallback"] = "keep_source_chunk"
    if not isinstance(payload.get("output"), dict):
        payload["output"] = {}
    payload["output"]["language"] = target_base

    runtime_contract_path = (
        Path(settings.BASE_DIR).parent
        / "data"
        / "editions"
        / str(edition.id)
        / "core"
        / f"contract_refine_{target_base}.json"
    )
    runtime_contract_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_contract_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return runtime_contract_path, refine_input_dir, out_dir


def _build_runtime_polish_contract(
    edition,
    target_language: str,
) -> tuple[Path, Path, Path]:
    payload = json.loads(_select_polish_contract(target_language).read_text(encoding="utf-8"))
    source_dir = _resolve_refine_output_dir(
        _resolve_refine_source_dir(edition, target_language)[0],
        refine_profile=_default_refine_profile_for_language(target_language),
        target_language=target_language,
    )
    if not source_dir.exists():
        raise FileNotFoundError(f"Polish source chunks not found: {source_dir}. Run Refine first.")

    polish_input_dir = (
        Path(settings.BASE_DIR).parent
        / "data"
        / "editions"
        / str(edition.id)
        / "core"
        / f"polish_input_{utils.normalize_lang(target_language)}"
    )
    polish_input_dir.mkdir(parents=True, exist_ok=True)
    for stale in polish_input_dir.glob("*.txt"):
        stale.unlink()

    source_chunks = [
        p for p in sorted(source_dir.glob("*.txt"))
        if not (p.name == "merged.txt" or p.name.startswith("merged_"))
    ]
    if not source_chunks:
        raise FileNotFoundError(f"No refine chunks found in {source_dir} for polish input.")
    for path in source_chunks:
        shutil.copyfile(path, polish_input_dir / path.name)

    out_dir = source_dir.parent / POLISH_RETURN_DIRNAME
    payload["chunk_dir"] = str(polish_input_dir)
    payload["out_dir"] = str(out_dir)
    payload["target_language"] = utils.normalize_lang(target_language)
    payload["max_output_tokens"] = _recommended_translate_max_output_tokens(
        polish_input_dir,
        "*.txt",
        target_language,
        current_limit=payload.get("max_output_tokens"),
    )
    payload["sanitize_failure_fallback"] = "keep_source_chunk"
    if not isinstance(payload.get("output"), dict):
        payload["output"] = {}
    payload["output"]["language"] = utils.normalize_lang(target_language)

    runtime_contract_path = (
        Path(settings.BASE_DIR).parent
        / "data"
        / "editions"
        / str(edition.id)
        / "core"
        / f"contract_polish_{utils.normalize_lang(target_language)}.json"
    )
    runtime_contract_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_contract_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return runtime_contract_path, polish_input_dir, out_dir


def _resolve_core_path(path_value: str) -> Path:
    candidate = Path(path_value)
    if candidate.is_absolute():
        return candidate
    return Path(settings.BASE_DIR).parent / candidate


def _copy_merge_to_build(edition, merged_path: Path, target_path: Path) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(merged_path, target_path)
    return target_path


def _legacy_gaiden_merge_path(book_id: int, language: str, stage: str) -> Path | None:
    lang = utils.normalize_lang(language)
    base_dir = Path("data/chunks") / f"book_{book_id:04d}" / f"refine_{lang}_01"
    if not base_dir.exists():
        return None

    if stage == "polish":
        candidates = [
            base_dir / "merged_polished_en_2025.txt",
            base_dir / "merged_polished_en.txt",
        ]
    elif stage == "refine":
        candidates = [
            base_dir / f"merged_refined_{lang}_2025.txt",
            base_dir / f"merged_refined_{lang}.txt",
        ]
    else:
        candidates = [
            base_dir / f"merged_{lang}_2025.txt",
            base_dir / f"merged_{lang}.txt",
        ]
        if lang == "en":
            candidates.insert(0, base_dir / "merged_en_modern_2025.txt")
            candidates.insert(0, base_dir / "merged_en_modern_2026.txt")

    for path in candidates:
        if path.exists():
            return path
    return None


def _detect_merged_path(out_dir: Path) -> Path | None:
    lang_key = out_dir.name
    merged = out_dir / f"merged_{lang_key}.txt"
    if merged.exists():
        return merged
    alt = out_dir / "merged.txt"
    if alt.exists():
        return alt
    candidates = sorted(out_dir.glob("merged_*.txt"))
    if candidates:
        return candidates[0]
    return None


def _count_split_chunks(book_code: str) -> int | None:
    book_id = _parse_book_id(book_code)
    if book_id is None:
        return None
    chunks_dir = storage.split_01_dir(f"book_{book_id:04d}")
    if not chunks_dir.is_dir():
        return None
    return len(list(chunks_dir.glob("*.txt")))


def _assets_language_for_edition(edition, pipeline_state: EditionPipeline | None) -> str:
    lang_code = edition.language.code
    if (
        pipeline_state
        and pipeline_state.frontmatter_locked
        and pipeline_state.frontmatter_language
    ):
        lang_code = pipeline_state.frontmatter_language
    return utils.normalize_lang(lang_code)


def _images_dir_for_edition(edition, pipeline_state: EditionPipeline | None) -> Path:
    lang_code = _assets_language_for_edition(edition, pipeline_state)
    return (
        Path(settings.BASE_DIR).parent
        / "data"
        / "images"
        / edition.work.code
        / lang_code
    )


def _consolidated_images_dir_for_edition(edition) -> Path:
    return paths.edition_build_dir(edition) / "assets" / "images"


def _internal_images_disabled_marker(edition) -> Path:
    return paths.edition_build_dir(edition) / "COVER_ONLY"


def _internal_images_end_only_marker(edition) -> Path:
    return paths.edition_build_dir(edition) / "END_IMAGES"


def _internal_images_disabled_for_edition(edition) -> bool:
    return _internal_images_disabled_marker(edition).exists() and not _internal_images_end_only_for_edition(edition)


def _internal_images_end_only_for_edition(edition) -> bool:
    return _internal_images_end_only_marker(edition).exists()


def _extract_images_zip(images_zip, target_dir: Path) -> int:
    extracted = 0
    with zipfile.ZipFile(images_zip) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            rel = Path(member.filename)
            if rel.is_absolute() or ".." in rel.parts:
                continue
            suffix = rel.suffix.lower()
            if suffix not in md_transform.IMAGE_EXTENSIONS:
                continue
            target = target_dir / rel.name
            with archive.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted += 1
    return extracted


def _normalize_uploaded_image_stem(name: str) -> str:
    stem = Path(name).stem.strip().lower()
    if not stem:
        return "image"
    if stem.isdigit():
        return f"{int(stem):02d}"
    nums = re.findall(r"\d+", stem)
    if nums:
        return f"{int(nums[0]):02d}"
    clean = re.sub(r"[^a-z0-9._-]+", "_", stem).strip("._-")
    return clean or "image"


def _is_cover_upload_name(name: str) -> bool:
    stem = _normalize_uploaded_image_stem(name)
    if stem in {"00", "0", "cover", "capa", "frontcover"}:
        return True
    nums = re.findall(r"\d+", stem)
    return bool(nums) and int(nums[0]) == 0


def _unique_jpg_name(stem: str, used: set[str]) -> str:
    base = stem
    idx = 1
    candidate = f"{base}.jpg"
    while candidate in used:
        idx += 1
        candidate = f"{base}_{idx:02d}.jpg"
    used.add(candidate)
    return candidate


def _convert_uploaded_images_to_jpg(files, target_dir: Path) -> tuple[int, list[Path]]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow nao instalado.") from exc

    converted = 0
    outputs: list[Path] = []
    skipped_cover = 0
    used_names = {p.name for p in target_dir.glob("*.jpg")}
    for uploaded in files:
        if not uploaded:
            continue
        if _is_cover_upload_name(getattr(uploaded, "name", "")):
            skipped_cover += 1
            continue
        stem = _normalize_uploaded_image_stem(getattr(uploaded, "name", ""))
        out_name = _unique_jpg_name(stem, used_names)
        out_path = target_dir / out_name
        try:
            with Image.open(uploaded) as img:
                rgb = img.convert("RGB")
                rgb.save(out_path, format="JPEG", quality=95, optimize=True)
            converted += 1
            outputs.append(out_path)
        except Exception:
            continue
    return converted, outputs, skipped_cover


def _consolidate_internal_images(images_dir: Path, consolidated_dir: Path) -> dict[str, int | str]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow nao instalado.") from exc

    source_images = md_transform.list_available_images(images_dir)
    source_images = [p for p in source_images if not _is_cover_upload_name(p.name)]
    if not source_images:
        return {
            "source_count": 0,
            "consolidated_count": 0,
            "manifest_path": "",
            "dir": str(consolidated_dir),
        }

    if consolidated_dir.exists():
        shutil.rmtree(consolidated_dir)
    consolidated_dir.mkdir(parents=True, exist_ok=True)

    used_names: set[str] = set()
    seq = 1
    rows: list[dict[str, str | int]] = []
    consolidated_count = 0

    for src in source_images:
        numbers = re.findall(r"\d+", src.stem)
        if numbers:
            chapter = int(numbers[0])
            slot = int(numbers[1]) if len(numbers) >= 2 else 1
        else:
            chapter = seq
            slot = 1
            seq += 1

        out_name = f"ch{chapter:02d}_{slot:02d}.jpg"
        while out_name in used_names:
            slot += 1
            out_name = f"ch{chapter:02d}_{slot:02d}.jpg"
        used_names.add(out_name)
        out_path = consolidated_dir / out_name

        with Image.open(src) as img:
            rgb = img.convert("RGB")
            rgb.save(out_path, format="JPEG", quality=95, optimize=True)

        consolidated_count += 1
        rows.append(
            {
                "source": str(src),
                "output": str(out_path),
                "chapter": chapter,
                "slot": slot,
            }
        )

    manifest_path = consolidated_dir / "images_map.json"
    manifest = {
        "schema": "images_consolidation_v1",
        "source_dir": str(images_dir),
        "consolidated_dir": str(consolidated_dir),
        "items": rows,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "source_count": len(source_images),
        "consolidated_count": consolidated_count,
        "manifest_path": str(manifest_path),
        "dir": str(consolidated_dir),
    }


def edition_steps(request, edition_id: int):
    edition = get_object_or_404(EditorialEdition, id=edition_id)
    book_code, language = _edition_codes(edition)
    normalized_language = utils.normalize_lang(language)
    source_template = (
        BookEditionTemplate.objects.filter(book_code=book_code, language=normalized_language).first()
    )
    if source_template is None:
        messages.warning(request, "Cadastro obrigatorio: preencha a Etapa 1 antes de acessar o pipeline.")
        return redirect("book_edition_edit", book_code=book_code, language=normalized_language)

    source_format = _source_format_from_template(source_template)
    allow_html_to_common = request.GET.get("allow_html_to_common") == "1"
    if request.method == "GET" and source_format == SOURCE_FORMAT_HTML and not allow_html_to_common:
        root = Path(settings.BASE_DIR).parent
        html_artifacts = _html_artifact_paths(book_code, normalized_language, source_format)
        md_source_exists = (root / html_artifacts["md_source"]).exists()
        if not md_source_exists:
            return redirect("pipeline_html_dashboard", edition_id=edition.id)

    def _redirect_editorial():
        state = EditionPipeline.objects.filter(edition=edition).first()
        current_lang = utils.normalize_lang(
            request.POST.get("target_lang")
            or request.POST.get("md_language")
            or request.POST.get("target_language")
            or (state.frontmatter_language if state and state.frontmatter_language else "")
            or (state.md_language if state and state.md_language else "")
            or (state.translation_language if state and state.translation_language else "")
            or language
        )
        try:
            target_edition = _edition_for_language(edition, current_lang)
        except Exception:
            target_edition = edition
        locked = bool((state and state.frontmatter_locked) or request.POST.get("frontmatter_lock") == "1")
        return redirect(
            f"{_edition_steps_redirect_url(target_edition, frontmatter_lang=current_lang, frontmatter_locked=locked)}#transformacao-editorial"
        )

    texts = EditionText.objects.filter(edition=edition).first()
    raw_path = (texts.raw_path if texts else "") or edition.raw_source_path

    def _core_text() -> str:
        if texts and getattr(texts, "normalized_text", ""):
            return texts.normalized_text
        if texts and getattr(texts, "normalized_path", ""):
            path = Path(texts.normalized_path)
            if path.exists():
                return path.read_text(encoding="utf-8")
        if texts and getattr(texts, "raw_text", ""):
            return texts.raw_text
        if raw_path:
            path = Path(raw_path)
            if path.exists():
                return path.read_text(encoding="utf-8")
        return ""

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "run_matrix":
            if PipelineRun is None or PipelineRunItem is None:
                messages.error(request, "Runner Matrix indisponivel nesta branch (models PipelineRun ausentes).")
                return redirect("edition_steps", edition_id=edition.id)
            matrix_action = (request.POST.get("matrix_action") or "TRANSLATE").strip().upper()
            matrix_lang = utils.normalize_lang(request.POST.get("matrix_lang") or language)
            matrix_mode = (request.POST.get("matrix_mode") or "automatic").strip().lower()
            dry_run = request.POST.get("matrix_dry_run") == "1"
            skip_existing = request.POST.get("matrix_skip_existing") == "1"

            allowed_actions = {
                "NORMALIZE",
                "CHUNK",
                "TRANSLATE",
                "TRANSLATE_DEFAULT",
                "SPLIT_FOR_REFINE",
                "BUILD",
                "EXPORT_EPUB",
            }
            if matrix_action not in allowed_actions:
                messages.error(request, f"Acao de matrix invalida: {matrix_action}")
                return redirect("edition_steps", edition_id=edition.id)

            book_id = _parse_book_id(book_code)
            if book_id is None:
                messages.error(request, "book_code invalido para matrix.")
                return redirect("edition_steps", edition_id=edition.id)

            options = {
                "skip_existing": skip_existing,
                "stop_on_error": False,
                "dry_run": dry_run,
                "selected_mode": matrix_mode,
                "translate_mode": matrix_mode,
                "split_mode": "auto",
                "refine_mode": "auto",
            }
            run = PipelineRun.objects.create(
                mode="MATRIX",
                action=matrix_action,
                options=options,
                status="PENDING",
            )
            item = PipelineRunItem.objects.create(
                run=run,
                book_id=book_id,
                book_code=book_code,
                lang=matrix_lang,
                status="PENDING",
            )

            try:
                call_command("run_pipeline_matrix", str(run.id))
            except Exception as exc:
                run.refresh_from_db()
                item.refresh_from_db()
                messages.error(
                    request,
                    f"Runner Matrix falhou (run {run.id}): {exc}",
                )
                if item.log_path:
                    messages.error(request, f"Log: {item.log_path}")
                return redirect("edition_steps", edition_id=edition.id)

            run.refresh_from_db()
            item.refresh_from_db()
            messages.success(
                request,
                (
                    f"Runner Matrix concluido: run {run.id} | "
                    f"acao={matrix_action} | lang={matrix_lang} | "
                    f"item={item.status}"
                ),
            )
            if item.log_path:
                messages.info(request, f"Log: {item.log_path}")
            return redirect("edition_steps", edition_id=edition.id)

        if action == "upload_cover":
            cover_file = request.FILES.get("cover_file")
            if not cover_file:
                messages.error(request, "Selecione uma imagem de capa.")
                return _redirect_editorial()
            try:
                from PIL import Image
            except ImportError:
                messages.error(request, "Pillow nao instalado. Nao foi possivel converter a capa.")
                return redirect("edition_steps", edition_id=edition.id)

            pipeline_state = EditionPipeline.objects.filter(edition=edition).first()
            book_code = edition.work.code
            lang_code = edition.language.code
            if (
                pipeline_state
                and pipeline_state.frontmatter_locked
                and pipeline_state.frontmatter_language
            ):
                lang_code = pipeline_state.frontmatter_language
            cover_dir = (
                Path(settings.BASE_DIR).parent
                / "data"
                / "covers"
                / book_code
                / lang_code
            )
            cover_dir.mkdir(parents=True, exist_ok=True)
            cover_path = cover_dir / "cover.jpg"

            with Image.open(cover_file) as img:
                rgb = img.convert("RGB")
                rgb.save(cover_path, format="JPEG", quality=95, optimize=True)

            try:
                rel_path = cover_path.relative_to(Path(settings.BASE_DIR).parent)
                edition.cover_filepath = str(rel_path)
            except ValueError:
                edition.cover_filepath = str(cover_path)
            edition.save(update_fields=["cover_filepath"])
            messages.success(request, f"Capa salva: {edition.cover_filepath}")
            return _redirect_editorial()
        if action in {"upload_images_zip", "upload_gallery_zip"}:
            if _internal_images_disabled_for_edition(edition):
                messages.info(request, "Este livro esta configurado como cover only. Imagens internas ficam desativadas.")
                return _redirect_editorial()
            images_zip = request.FILES.get("images_zip")
            if not images_zip:
                messages.error(request, "Selecione um arquivo ZIP com imagens.")
                return _redirect_editorial()

            pipeline_state = EditionPipeline.objects.filter(edition=edition).first()
            images_dir = _images_dir_for_edition(edition, pipeline_state)
            if images_dir.exists():
                shutil.rmtree(images_dir)
            images_dir.mkdir(parents=True, exist_ok=True)

            try:
                extracted = _extract_images_zip(images_zip, images_dir)
            except zipfile.BadZipFile:
                messages.error(request, "Arquivo ZIP invalido.")
                return _redirect_editorial()

            if extracted == 0:
                messages.warning(request, "ZIP processado, mas nenhuma imagem valida foi encontrada.")
            else:
                messages.success(request, f"Imagens importadas: {extracted}")
            messages.info(request, f"Diretorio de imagens: {images_dir}")
            return _redirect_editorial()
        if action in {"upload_images_files", "upload_gallery_files"}:
            if _internal_images_disabled_for_edition(edition):
                messages.info(request, "Este livro esta configurado como cover only. Imagens internas ficam desativadas.")
                return _redirect_editorial()
            files = request.FILES.getlist("images_files")
            if not files:
                messages.error(request, "Selecione uma ou mais imagens.")
                return _redirect_editorial()

            pipeline_state = EditionPipeline.objects.filter(edition=edition).first()
            images_dir = _images_dir_for_edition(edition, pipeline_state)
            if images_dir.exists():
                shutil.rmtree(images_dir)
            images_dir.mkdir(parents=True, exist_ok=True)

            try:
                converted, outputs, skipped_cover = _convert_uploaded_images_to_jpg(files, images_dir)
            except RuntimeError as exc:
                messages.error(request, str(exc))
                return _redirect_editorial()

            if converted == 0:
                messages.error(request, "Nenhuma imagem valida foi convertida para JPG.")
                return _redirect_editorial()

            messages.success(request, f"Imagens convertidas para JPG: {converted}")
            if skipped_cover:
                messages.info(
                    request,
                    "Arquivos de capa (00/cover) foram ignorados aqui. Use o bloco Cover para a capa.",
                )
            messages.info(request, f"Diretorio de imagens: {images_dir}")
            return _redirect_editorial()
        if action in {"consolidate_images", "consolidate_gallery_images"}:
            if _internal_images_disabled_for_edition(edition):
                messages.info(request, "Este livro esta configurado como cover only. Imagens internas ficam desativadas.")
                return _redirect_editorial()
            pipeline_state = EditionPipeline.objects.filter(edition=edition).first()
            images_dir = _images_dir_for_edition(edition, pipeline_state)
            if not images_dir.exists():
                messages.error(request, f"Diretorio de imagens nao encontrado: {images_dir}")
                return _redirect_editorial()

            consolidated_dir = _consolidated_images_dir_for_edition(edition)
            try:
                result = _consolidate_internal_images(images_dir, consolidated_dir)
            except RuntimeError as exc:
                messages.error(request, str(exc))
                return _redirect_editorial()

            if int(result.get("consolidated_count", 0)) == 0:
                messages.warning(request, "Nenhuma imagem interna foi consolidada.")
            else:
                messages.success(
                    request,
                    (
                        f"Imagens consolidadas: {result.get('consolidated_count')} "
                        f"(de {result.get('source_count')})"
                    ),
                )
                messages.info(request, f"Consolidado: {result.get('dir')}")
                messages.info(request, f"Mapeamento: {result.get('manifest_path')}")
            return _redirect_editorial()
        if action == "save_core_txt":
            core_text = _core_text()
            if not core_text.strip():
                messages.error(request, "Core vazio. Nada para salvar.")
                return redirect("edition_steps", edition_id=edition.id)
            out_path = paths.core_last_txt_path(edition)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(core_text, encoding="utf-8")
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
            try:
                rel_path = out_path.relative_to(Path(settings.BASE_DIR).parent)
                pipeline_state.core_last_txt_path = str(rel_path)
            except ValueError:
                pipeline_state.core_last_txt_path = str(out_path)
            pipeline_state.save(update_fields=["core_last_txt_path"])
            messages.success(request, f"Core salvo: {pipeline_state.core_last_txt_path}")
            return redirect("edition_steps", edition_id=edition.id)
        if action == "save_translation_language":
            target_language = _normalize_translate_variant(request.POST.get("target_language") or language)
            target_base = _translate_base_language(target_language)
            try:
                target_edition = _edition_for_language(edition, target_base)
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("edition_steps", edition_id=edition.id)
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=target_edition)
            pipeline_state.translation_language = target_language
            pipeline_state.md_language = target_base
            pipeline_state.save(update_fields=["translation_language", "md_language"])
            messages.info(
                request,
                f"Idioma salvo ({_translate_variant_label(target_language)}). Refine ou Next Step.",
            )
            result = md_transform.run_txt_to_md(target_edition, language_override=target_base)
            items = result.get("items") or []
            if len(items) > 1:
                outputs = ", ".join(f"{item['language']}: {item['path']}" for item in items)
                msg = f"TXT to MD OK: {outputs}"
            else:
                msg = f"TXT to MD OK: {result['path']}"
                if result.get("path_pre_qa"):
                    msg = f"{msg} (PRE_QA: {result['path_pre_qa']})"
            messages.success(request, msg)
            return redirect(
                f"{reverse('edition_steps', kwargs={'edition_id': target_edition.id})}#transformacao-editorial"
            )
        if action == "insert_headlines":
            build_dir = paths.edition_build_dir(edition)
            md_targets = sorted(build_dir.glob("BOOK.PRE_QA*.md"))
            md_targets = [path for path in md_targets if path.is_file()]
            if not md_targets:
                messages.error(
                    request,
                    "BOOK.PRE_QA nao encontrado. Rode TXT -> MD antes de inserir headlines.",
                )
                return _redirect_editorial()
            for md_path in md_targets:
                out_path = md_path
                lang = language.lower()
                if md_path.name.startswith("BOOK.PRE_QA."):
                    lang = md_path.name.split(".", 2)[-1].lower()
                    out_path = md_path.with_name(f"BOOK.PRE_EDITION.{lang}.md")
                else:
                    out_path = paths.pre_edition_md_path(edition)
                out_path.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
                md_transform.insert_page_headlines(out_path, lang=lang)
            messages.success(
                request,
                "Headlines de capitulo inseridos no PRE_EDITION.",
            )
            return _redirect_editorial()
        if action == "insert_images":
            if _internal_images_disabled_for_edition(edition) or _internal_images_end_only_for_edition(edition):
                messages.info(request, "Este livro esta configurado para manter figuras fora do miolo. Placeholders internos nao serao inseridos.")
                return _redirect_editorial()
            build_dir = paths.edition_build_dir(edition)
            md_targets = sorted(build_dir.glob("BOOK.PRE_EDITION*"))
            md_targets = [path for path in md_targets if path.is_file()]
            if not md_targets:
                messages.error(
                    request,
                    "BOOK.PRE_EDITION nao encontrado. Rode headlines antes de inserir imagens.",
                )
                return _redirect_editorial()
            for md_path in md_targets:
                md_transform.insert_image_placeholders(md_path)
            messages.success(
                request,
                "Placeholders de imagem inseridos no PRE_EDITION.",
            )
            return _redirect_editorial()
        if action in {"apply_images", "apply_gallery_images"}:
            if _internal_images_disabled_for_edition(edition):
                messages.info(request, "Este livro esta configurado como cover only. Imagens internas nao serao aplicadas ao miolo.")
                return _redirect_editorial()
            pipeline_state = EditionPipeline.objects.filter(edition=edition).first()
            raw_images_dir = _images_dir_for_edition(edition, pipeline_state)
            consolidated_dir = _consolidated_images_dir_for_edition(edition)
            images_dir = consolidated_dir if consolidated_dir.exists() else raw_images_dir
            images = md_transform.list_available_images(images_dir)
            if not images:
                messages.error(
                    request,
                    f"Nenhuma imagem encontrada em {images_dir}. Suba as imagens antes.",
                )
                return _redirect_editorial()

            build_dir = paths.edition_build_dir(edition)
            md_targets = sorted(build_dir.glob("BOOK.PRE_EDITION*"))
            md_targets = [path for path in md_targets if path.is_file()]
            if not md_targets:
                messages.error(
                    request,
                    "BOOK.PRE_EDITION nao encontrado. Rode headlines/placeholders antes de inserir imagens.",
                )
                return _redirect_editorial()

            if _internal_images_end_only_for_edition(edition):
                total_inserted = 0
                for md_path in md_targets:
                    result = md_transform.append_images_gallery_to_pre_edition(md_path, images_dir)
                    total_inserted += int(result.get("inserted", 0))
                if total_inserted:
                    messages.success(
                        request,
                        f"Imagens adicionadas ao fim do livro: {total_inserted}",
                    )
                else:
                    messages.warning(request, "Nenhuma imagem interna disponivel para anexar ao fim do livro.")
                messages.info(request, f"Fonte das imagens: {images_dir}")
                return _redirect_editorial()

            total_inserted = 0
            total_placeholders = 0
            total_unresolved = 0
            total_existing_refs = 0
            already_applied = False
            for md_path in md_targets:
                result = md_transform.apply_images_to_pre_edition(md_path, images_dir)
                total_inserted += int(result.get("inserted", 0))
                total_placeholders += int(result.get("placeholders_total", 0))
                total_unresolved += int(result.get("unresolved", 0))
                total_existing_refs += int(result.get("existing_refs", 0))
                already_applied = already_applied or bool(int(result.get("already_applied", 0)))

            if total_inserted:
                messages.success(
                    request,
                    f"Imagens inseridas no PRE_EDITION: {total_inserted}/{total_placeholders}",
                )
            elif already_applied and total_existing_refs > 0:
                messages.success(
                    request,
                    f"Imagens ja aplicadas no PRE_EDITION: {total_existing_refs} referencia(s).",
                )
            else:
                messages.warning(
                    request,
                    "Nenhum placeholder foi substituido por imagem. Rode 'Inserir placeholders' antes.",
                )
            if total_unresolved:
                messages.warning(
                    request,
                    f"Placeholders sem imagem: {total_unresolved}",
                )
            messages.info(request, f"Fonte das imagens: {images_dir}")
            return _redirect_editorial()

    legacy_merges.sync_legacy_merges_from_translated(edition)
    sync_log = []

    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
    raw_name = Path(raw_path).name if raw_path else None

    def _status(flag: bool) -> str:
        return "OK" if flag else "falta"

    split_by_chapter_dir = paths.split_by_chapter_dir(edition)
    split_by_chapter_manifest_path = split_by_chapter_dir / "manifest.json"

    chunk_count = _count_split_chunks(book_code)
    pipeline_prereqs = _pipeline01_prereq_state(edition)
    pipeline01_steps = build_pipeline01_steps(edition, pipeline_state)
    heading_clean_path = heading_cleaner.clean_path_for_book_code(
        str(pipeline_prereqs["book_code"])
    )
    heading_cleaner_done = bool(pipeline_prereqs["heading_clean_exists"])
    translate_step = next((s for s in pipeline01_steps if s.get("key") == "translate"), None)
    can_translate = bool(translate_step and translate_step.get("can_run"))

    pre_edition_path = paths.pre_edition_md_path(edition)
    pre_qa_path = paths.pre_qa_md_path(edition)
    qa_path = paths.qa_md_path(edition)
    final_md_path = paths.final_md_path(edition)
    build_md_path = paths.build_md_path(edition)
    epub_path = paths.epub_path(edition)
    pdf_path = paths.pdf_path(edition)
    qa_log_path = paths.qa_log_path(edition)
    refine_qa_json_path = paths.refine_qa_json_path(edition)
    refine_qa_md_path = paths.refine_qa_md_path(edition)
    miolo_paths = []
    miolo_path = paths.miolo_md_path(edition)
    if miolo_path.exists():
        miolo_paths.append(
            {
                "language": language,
                "path": str(miolo_path),
                "label": miolo_path.name,
            }
        )

    if final_md_path.exists():
        md_status = "QA_DONE"
    elif pre_qa_path.exists():
        md_status = "PRE_QA"
    else:
        md_status = "NONE"

    md_preview = ""
    if qa_path.exists():
        preview_path = qa_path
    elif pre_edition_path.exists():
        preview_path = pre_edition_path
    else:
        preview_path = pre_qa_path
    if preview_path.exists():
        md_preview = preview_path.read_text(encoding="utf-8")[:10000]

    issues = []
    if qa_log_path.exists():
        try:
            issues = json.loads(qa_log_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            issues = []

    refine_qa_status = "falta"
    refine_qa_summary: dict | None = None
    if refine_qa_json_path.exists():
        try:
            refine_report = json.loads(refine_qa_json_path.read_text(encoding="utf-8"))
            refine_qa_summary = (
                refine_report.get("summary")
                if isinstance(refine_report.get("summary"), dict)
                else None
            )
            refine_qa_status = "OK" if bool(refine_report.get("pass")) else "FAIL"
        except json.JSONDecodeError:
            refine_qa_status = "invalid"

    frontmatter_langs = [choice[0] for choice in BookEditionTemplate.LANG_CHOICES]
    frontmatter_lang_param = utils.normalize_lang(request.GET.get("frontmatter_lang") or "")
    frontmatter_locked = request.GET.get("frontmatter_lock") == "1"
    if frontmatter_lang_param in frontmatter_langs:
        pipeline_state.frontmatter_language = frontmatter_lang_param
        pipeline_state.frontmatter_locked = frontmatter_locked
        pipeline_state.save(update_fields=["frontmatter_language", "frontmatter_locked"])

    frontmatter_lang = (
        frontmatter_lang_param
        or pipeline_state.frontmatter_language
        or pipeline_state.md_language
        or pipeline_state.translation_language
        or language
    )
    if frontmatter_lang not in frontmatter_langs:
        frontmatter_lang = language
    if pipeline_state.frontmatter_locked and pipeline_state.frontmatter_language:
        frontmatter_lang = pipeline_state.frontmatter_language
        frontmatter_locked = True

    editorial_templates = _ensure_editorial_templates_for_all_languages(edition)
    frontmatter_template, created = BookEditionTemplate.objects.get_or_create(
        book_code=book_code,
        language=frontmatter_lang,
        defaults=_frontmatter_template_defaults(edition, frontmatter_lang),
    )
    if created:
        frontmatter_template.save()
    editorial_language_rows = _editorial_language_rows(editorial_templates)
    block_statuses = _block_status_map(
        pipeline_state=pipeline_state,
        raw_path=raw_path,
        frontmatter_template=frontmatter_template,
        md_final_exists=final_md_path.exists(),
        build_exists=build_md_path.exists(),
        epub_exists=epub_path.exists(),
        pdf_exists=pdf_path.exists(),
    )
    preflight_json = paths.preflight_json_path(edition)
    preflight_md = paths.preflight_md_path(edition)
    preflight_done = preflight_json.exists() and preflight_md.exists()
    preflight_status_tone = "ok"
    preflight_status_label = "OK"
    preflight_status_note = ""
    if preflight_done:
        preflight_status_tone, preflight_status_label, preflight_status_note = _preflight_step_status(preflight_json)
    preflight_can_run, preflight_block_reason = _preflight_gate(edition, frontmatter_template)
    preflight_step = {
        "title": "Pre-producao (Pre-flight)",
        "run_url": reverse("pipeline_preflight_run", kwargs={"edition_id": edition.id}),
        "button_label": "Rerodar Pre-flight" if preflight_done else "Rodar Pre-flight",
        "can_run": preflight_can_run,
        "done": preflight_done,
        "status_tone": preflight_status_tone,
        "status_label": preflight_status_label,
        "block_reason": preflight_block_reason,
        "outputs": [
            _rel_project_path(preflight_json),
            _rel_project_path(preflight_md),
        ],
        "notes": " ".join(
            part
            for part in [
                "Analise editorial/estrutural no fim do Bloco 03, antes do MD final e do build.",
                preflight_status_note,
            ]
            if part
        ),
    }

    def _resolve_md_source_path(lang: str) -> str:
        build_dir = paths.edition_build_dir_for_language(book_code, lang)
        if not build_dir.exists():
            return ""
        marker = build_dir / paths.FORCE_MERGE_TRANSLATE_MARKER
        if marker.exists():
            order = ["merge_translate", "merge_refine", "merge_polish"]
        else:
            order = [p.replace(".txt", "") for p in paths.MERGE_PRIORITY]
        candidates: list[Path] = []
        for base in order:
            candidates.append(build_dir / f"{base}_{lang}.txt")
            candidates.append(build_dir / f"{base}.txt")
        for path in candidates:
            if path.exists():
                return str(path)
        for path in sorted(build_dir.glob("*.txt")):
            return str(path)
        return ""

    md_language_default = (
        request.POST.get("md_language")
        or pipeline_state.md_language
        or language
    )
    refine_profile = _normalized_refine_profile_for_language(
        pipeline_state.refine_profile or _default_refine_profile_for_language(edition.language.code),
        edition.language.code,
    )
    refine_profile_options = [
        {
            "value": key,
            "label": REFINE_PROFILES[key]["label"],
            "agent_name": REFINE_PROFILES[key]["agent_name"],
            "description": REFINE_PROFILES[key]["description"],
        }
        for key in _refine_profile_keys_for_language(edition.language.code)
    ]
    md_source_map = {
        lang: _resolve_md_source_path(lang)
        for lang in ("en", "es", "ptbr", "de", "fr", "it")
    }
    md_source_map_json = json.dumps(md_source_map)
    translate_contract_map: dict[str, str] = {}
    translate_variant_options = list(TRANSLATE_VARIANT_OPTIONS)
    project_root = Path(settings.BASE_DIR).parent
    for option in translate_variant_options:
        lang = option["value"]
        try:
            contract_path = _select_contract_path(lang)
            try:
                translate_contract_map[lang] = str(contract_path.relative_to(project_root))
            except ValueError:
                translate_contract_map[lang] = str(contract_path)
        except ValueError:
            translate_contract_map[lang] = ""
    translate_contract_map_json = json.dumps(translate_contract_map)
    if PipelineRun is not None:
        matrix_runs = (
            PipelineRun.objects.filter(items__book_code=book_code)
            .distinct()
            .prefetch_related("items")
            .order_by("-id")[:8]
        )
    else:
        matrix_runs = []
    images_dir = _images_dir_for_edition(edition, pipeline_state)
    images_count = len(md_transform.list_available_images(images_dir))
    consolidated_images_dir = _consolidated_images_dir_for_edition(edition)
    consolidated_images_count = len(md_transform.list_available_images(consolidated_images_dir))
    consolidated_images_map = consolidated_images_dir / "images_map.json"
    internal_images_disabled = _internal_images_disabled_for_edition(edition)
    internal_images_end_only = _internal_images_end_only_for_edition(edition)
    build_history = list(
        EditionBuild.objects.filter(edition=edition, language_code=frontmatter_lang).order_by("-build_version", "-created_at")
    )

    context = {
        "edition": edition,
        "edition_steps_action_url": _edition_steps_redirect_url(edition),
        "source_format": source_format,
        "source_upload_url": reverse(
            "book_edition_upload",
            kwargs={"book_code": book_code, "language": normalized_language},
        ),
        "status": {
            "raw": _status(bool(raw_path)),
            "normalize": _status(bool(pipeline_state.normalized_at)),
            "heading_cleaner": _status(heading_cleaner_done),
            "split": _status(bool(pipeline_state.split_at)),
            "split_by_chapter": _status(split_by_chapter_manifest_path.exists()),
            "translate": _status(bool(pipeline_state.translated_at)),
            "refine": _status(bool(pipeline_state.refined_at)),
            "qa_refine": refine_qa_status,
            "polish": _status(bool(pipeline_state.polished_at)),
        },
        "raw_path": raw_path,
        "raw_name": raw_name,
        "translate_language": pipeline_state.translation_language or pipeline_state.md_language or language,
        "translate_variant_options": translate_variant_options,
        "refine_profile": refine_profile,
        "refine_profile_options": refine_profile_options,
        "chunk_count": chunk_count,
        "sync_log": sync_log,
        "md_status": md_status,
        "md_preview": md_preview,
        "md_pre_edition_path": str(pre_edition_path) if pre_edition_path.exists() else None,
        "md_pre_qa_path": str(pre_qa_path) if pre_qa_path.exists() else None,
        "md_final_path": str(final_md_path) if final_md_path.exists() else None,
        "miolo_paths": miolo_paths,
        "miolo_filename": paths.miolo_md_filename(),
        "qa_issues": issues,
        "build_status": "DONE" if build_md_path.exists() else "NONE",
        "build_path": str(build_md_path) if build_md_path.exists() else None,
        "epub_path": str(epub_path) if epub_path.exists() else None,
        "pdf_path": str(pdf_path) if pdf_path.exists() else None,
        "editorial_changed": bool(getattr(pipeline_state, "editorial_changed", False)),
        "build_outdated": bool(getattr(pipeline_state, "build_outdated", False)),
        "last_editorial_update_at": getattr(pipeline_state, "last_editorial_update_at", None),
        "last_built_at": getattr(pipeline_state, "last_built_at", None),
        "build_history": build_history,
        "book_code": book_code,
        "language": language,
        "frontmatter_lang": frontmatter_lang,
        "frontmatter_lang_choices": BookEditionTemplate.LANG_CHOICES,
        "frontmatter_template": frontmatter_template,
        "frontmatter_preview": frontmatter_template.frontispiece_rendered,
        "copyright_preview": frontmatter_template.copyright_rendered,
        "frontmatter_locked": frontmatter_locked,
        "editorial_language_rows": editorial_language_rows,
        "editorial_optional_warnings": optional_section_warnings(frontmatter_template, frontmatter_lang),
        "system_blocks": SYSTEM_BLOCKS,
        "core_block_key": CORE_BLOCK_KEY,
        "core_isolation_languages": CORE_ISOLATION_LANGUAGES,
        "block_statuses": block_statuses,
        "block_status_labels": BLOCK_STATUS_LABELS,
        "pipeline_stage": pipeline_state.current_stage,
        "pipeline_last_log": pipeline_state.last_log,
        "md_language_default": md_language_default,
        "md_source_map": md_source_map_json,
        "translate_contract_map": translate_contract_map_json,
        "core_last_txt_path": pipeline_state.core_last_txt_path,
        "heading_clean_path": str(heading_clean_path) if heading_cleaner_done else None,
        "pipeline_prereqs": {
            "normalized_v2_path": str(pipeline_prereqs["normalized_v2_path"]),
            "normalized_v2_exists": bool(pipeline_prereqs["normalized_v2_exists"]),
            "split_01_dir": str(pipeline_prereqs["split_01_dir"]),
            "split_01_count": int(pipeline_prereqs["split_01_count"]),
            "split_01_exists": bool(pipeline_prereqs["split_01_exists"]),
            "heading_clean_dir": str(pipeline_prereqs["heading_clean_dir"]),
            "heading_clean_count": int(pipeline_prereqs["heading_clean_count"]),
            "heading_clean_exists": bool(pipeline_prereqs["heading_clean_exists"]),
            "can_translate": bool(pipeline_prereqs["can_translate"]),
        },
        "pipeline01_steps": pipeline01_steps,
        "can_translate": can_translate,
        "refine_qa_status": refine_qa_status,
        "refine_qa_summary": refine_qa_summary,
        "refine_qa_json_path": str(refine_qa_json_path) if refine_qa_json_path.exists() else None,
        "refine_qa_md_path": str(refine_qa_md_path) if refine_qa_md_path.exists() else None,
        "cover_filepath": edition.cover_filepath,
        "internal_images_disabled": internal_images_disabled,
        "internal_images_end_only": internal_images_end_only,
        "images_dir_path": str(images_dir) if images_dir.exists() else None,
        "images_count": images_count,
        "images_consolidated_dir_path": str(consolidated_images_dir) if consolidated_images_dir.exists() else None,
        "images_consolidated_count": consolidated_images_count,
        "images_consolidated_map_path": str(consolidated_images_map) if consolidated_images_map.exists() else None,
        "matrix_runs": matrix_runs,
        "preflight_step": preflight_step,
    }

    return render(request, "pipeline/edition_steps.html", context)


def pipeline_heading_cleaner_run(request, edition_id: int):
    edition = get_object_or_404(EditorialEdition, id=edition_id)
    if request.method != "POST":
        return redirect(_edition_steps_redirect_url(edition))

    core_edition = _processing_base_edition(edition)
    core_book_code, _core_lang = _edition_codes(core_edition)
    try:
        normalized_path, normalized_source = _ensure_normalized_v2_for_heading_cleaner(core_edition)
        split_dir, stale_split_count = _invalidate_split_01_after_heading_cleaner(core_edition)
        agent_name = (request.POST.get("agent_name") or "MechanicalHeadingCleaner").strip()
        result = heading_cleaner.run_heading_cleaner(core_edition, agent_name=agent_name)

        pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=core_edition)
        pipeline_state.current_stage = PipelineStage.NORMALIZED
        pipeline_state.chunked_at = None
        pipeline_state.last_log = (
            f"{timezone.now().isoformat()} :: HEADING_CLEAN_READY :: {result['clean_path']}"
        )
        pipeline_state.save(update_fields=["current_stage", "chunked_at", "last_log"])

        messages.info(
            request,
            f"Prereq normalized_v2: {normalized_path} ({normalized_source})",
        )
        messages.info(
            request,
            f"Split invalidado: {split_dir} [chunks_removidos={stale_split_count}]",
        )
        messages.success(
            request,
            (
                f"HeadingCleaner OK ({result['engine']}): {result['clean_path']}"
            ),
        )
        messages.info(request, f"Report: {result['report_path']}")
    except Exception as exc:
        messages.error(request, f"HeadingCleaner failed: {exc}")
        pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=core_edition)
        pipeline_state.last_log = str(exc)
        pipeline_state.save(update_fields=["last_log"])

    if core_edition.id != edition.id:
        messages.info(request, f"HeadingCleaner executado na edicao core EN de {core_book_code}.")
    return redirect(_edition_steps_redirect_url(edition))


def pipeline_normalize_run(request, edition_id: int):
    return run_edition_step(request, edition_id, "normalize")


def pipeline_chunk_run(request, edition_id: int):
    return run_edition_step(request, edition_id, "chunk")


def pipeline_translate_run(request, edition_id: int):
    return run_edition_step(request, edition_id, "translate")


def pipeline_refine_run(request, edition_id: int):
    return run_edition_step(request, edition_id, "refine")


def pipeline_merge_refine_run(request, edition_id: int):
    return run_edition_step(request, edition_id, "merge_refine")


def pipeline_preflight_run(request, edition_id: int):
    return run_edition_step(request, edition_id, "preflight")


def _run_translate_step_local(edition, pipeline_state, *, target_language: str) -> dict[str, object]:
    translate_step = next(
        (s for s in build_pipeline01_steps(edition, pipeline_state) if s.get("key") == "translate"),
        None,
    )
    if not (translate_step and bool(translate_step.get("can_run"))):
        reason = (translate_step or {}).get("block_reason") or (
            "Prerequisito para Translate: rode HeadingCleaner e depois refaca split_01."
        )
        raise ValueError(reason)

    book_code, language = _edition_codes(edition)
    target_language = _normalize_translate_variant(target_language or language)
    target_base = _translate_base_language(target_language)
    target_edition = _edition_for_language(edition, target_base)
    book_id_for_run = _parse_book_id(_edition_codes(target_edition)[0])
    if book_id_for_run is not None:
        os.environ["GAIDEN_BOOK_ID"] = str(book_id_for_run)
    stage_policy.POLICY.assert_stage_allowed(target_edition, "translate")
    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=target_edition)
    source_dir_for_validation: Path | None = None
    runtime_contract_path, source_label = _build_runtime_translate_contract(
        target_edition,
        target_language,
    )
    run_translation_contract(runtime_contract_path)
    out_dir_path = _resolve_contract_out_dir(runtime_contract_path, target_edition)
    source_dir_for_validation, _input_glob, _source_label = _translate_source_chunks(
        _edition_codes(target_edition)[0]
    )

    _validate_runtime_chunk_outputs(source_dir_for_validation, out_dir_path, "Translate")
    merged_path = _detect_merged_path(out_dir_path)
    if merged_path is None:
        merged_path = out_dir_path / f"merged_{out_dir_path.name}.txt"
    merged_path, _merge_stats = canonical_merge.write_canonical_merge(
        source_dir_for_validation,
        out_dir_path,
        merged_path,
    )
    _copy_merge_to_build(
        target_edition,
        merged_path,
        paths.merge_translate_path(target_edition),
    )
    pipeline_state.current_stage = PipelineStage.TRANSLATED
    pipeline_state.translation_language = target_language
    pipeline_state.md_language = target_base
    pipeline_state.translated_at = timezone.now()
    pipeline_state.last_log = ""
    pipeline_state.save(update_fields=["current_stage", "translation_language", "md_language", "translated_at", "last_log"])
    return {
        "success": f"Translate OK ({_translate_variant_label(target_language)})",
        "info": [f"Translate source: {source_label}"],
        "target_language": target_language,
    }


def _run_refine_step_local(edition, pipeline_state, *, refine_profile: str | None = None) -> dict[str, object]:
    refine_step = next(
        (s for s in build_pipeline01_steps(edition, pipeline_state) if s.get("key") == "refine"),
        None,
    )
    if not (refine_step and bool(refine_step.get("can_run"))):
        raise ValueError(
            (refine_step or {}).get("block_reason") or "Prerequisito para Refine: rode Translate."
        )

    target_edition = edition
    stage_policy.POLICY.assert_stage_allowed(target_edition, "refine")
    target_language = utils.normalize_lang(target_edition.language.code)
    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=target_edition)
    translate_variant = _normalize_translate_variant(
        pipeline_state.translation_language or target_edition.language.code
    )
    refine_profile = _normalized_refine_profile_for_language(
        refine_profile or pipeline_state.refine_profile or _default_refine_profile_for_language(target_language)
        ,
        target_language,
    )
    refine_profile_cfg = _refine_profile_config(refine_profile)
    if pipeline_state.refine_profile != refine_profile:
        pipeline_state.refine_profile = refine_profile
        pipeline_state.save(update_fields=["refine_profile"])
    source_dir, refine_source_label = _resolve_refine_source_dir(target_edition, translate_variant)
    if not source_dir.exists():
        raise FileNotFoundError(
            f"Refine source chunks not found: {source_dir}. Run Translate or Split by Chapter first."
        )

    try:
        out_dir_path = _resolve_refine_output_dir(
            source_dir,
            refine_profile=refine_profile,
            target_language=target_language,
        )
        from gaiden.tools.aldebaran_refine_return import run_aldebaran_refine_return

        result = run_aldebaran_refine_return(
            chunk_dir=source_dir,
            out_dir=out_dir_path,
            merge_name=f"merge_refine_{target_language}.txt",
            agent_name=refine_profile_cfg["agent_name"],
        )
        merged_path = Path(result["merge_path"])
    except ModuleNotFoundError:
        runtime_contract_path, refine_input_dir, out_dir_path = _build_runtime_refine_contract(
            target_edition, translate_variant, refine_profile=refine_profile
        )
        run_translation_contract(runtime_contract_path)
        merged_candidates = [
            out_dir_path / f"merge_refine_{target_language}.txt",
            out_dir_path / "merge_refine.txt",
            out_dir_path / "merged_return_aldebaran.txt",
            out_dir_path / "merged_return_kaiser.txt",
            out_dir_path / "merged.txt",
        ]
        merged_candidates.extend(sorted(out_dir_path.glob("merged_*.txt")))
        merged_path = next((p for p in merged_candidates if p.exists()), None)
        result = {
            "agent_name": f"{refine_profile_cfg['agent_name']} (contract-fallback)",
            "source_dir": str(refine_input_dir),
            "report_path": str(runtime_contract_path),
            "merge_path": str(merged_path or (out_dir_path / f"merged_{out_dir_path.name}.txt")),
        }

    _validate_runtime_chunk_outputs(source_dir, out_dir_path, "Refine")
    if merged_path is None:
        merged_path = out_dir_path / f"merged_{out_dir_path.name}.txt"
    merged_path, _merge_stats = canonical_merge.write_canonical_merge(
        source_dir,
        out_dir_path,
        merged_path,
        book_code=target_edition.work.code,
        language=target_language,
    )
    _copy_merge_to_build(
        target_edition,
        merged_path,
        paths.merge_refine_path(target_edition),
    )
    pipeline_state.current_stage = PipelineStage.REFINED
    pipeline_state.refined_at = timezone.now()
    pipeline_state.last_log = (
        f"{timezone.now().isoformat()} :: REFINE :: {refine_profile} :: {result['agent_name']}"
    )
    pipeline_state.save(update_fields=["current_stage", "refined_at", "last_log", "refine_profile"])
    return {
        "success": f"Refine OK ({refine_profile_cfg['label']} · {result['agent_name']})",
        "info": [
            f"Refine source ({refine_source_label}): {result['source_dir']}",
            f"Refine report: {result['report_path']}",
        ],
        "refine_profile": refine_profile,
    }


def _run_polish_step_local(edition) -> dict[str, object]:
    from gaiden.polish_en_2025 import run_polish_en_merged_file

    target_edition = edition
    stage_policy.POLICY.assert_stage_allowed(target_edition, "polish")
    book_code, _language = _edition_codes(target_edition)
    book_id = _parse_book_id(book_code)
    if book_id is None:
        raise ValueError("book_code must be like book_0001 to polish.")
    if utils.normalize_lang(target_edition.language.code) != "en":
        raise ValueError("Polish is only available for English.")

    EditionPipeline.objects.get_or_create(edition=target_edition)
    lang_key = "en_modern_2026"
    out_path = paths.merge_polish_path(target_edition)
    source_path = (
        Path(settings.BASE_DIR).parent
        / "data"
        / "translated"
        / book_code
        / "merge_refine_clean.txt"
    )
    if not source_path.exists():
        source_path = paths.merge_refine_path(target_edition)

    if not source_path.exists():
        raise FileNotFoundError("Polish Return source not found: merge_refine_clean.txt or merge_refine.txt")
    run_polish_en_merged_file(
        book_id=book_id,
        lang_key=lang_key,
        source_path=source_path,
        output_path=out_path,
    )
    source_info = str(source_path)

    _copy_merge_to_build(
        target_edition,
        out_path,
        paths.merge_polish_path(target_edition),
    )
    pipeline_state.current_stage = PipelineStage.POLISHED
    pipeline_state.polished_at = timezone.now()
    pipeline_state.last_log = ""
    pipeline_state.save()
    return {
        "success": "Polish Return OK",
        "info": [
            f"Polish source: {source_info}",
            f"Polish merge: {out_path}",
        ],
    }


def execute_language_isolated_core_step(
    *,
    edition_id: int,
    step: str,
    target_language: str | None = None,
    refine_profile: str | None = None,
) -> dict[str, object]:
    edition = EditorialEdition.objects.get(id=edition_id)
    pipeline_state = EditionPipeline.objects.filter(edition=edition).first()
    if step == "translate":
        return _run_translate_step_local(edition, pipeline_state, target_language=target_language or edition.language.code)
    if step == "refine":
        return _run_refine_step_local(edition, pipeline_state, refine_profile=refine_profile)
    if step in {"polish", "polish_return"}:
        return _run_polish_step_local(edition)
    raise ValueError(f"Unsupported isolated core step: {step}")


def run_edition_step(request, edition_id: int, step: str):
    edition = get_object_or_404(EditorialEdition, id=edition_id)
    book_code, language = _edition_codes(edition)

    if request.method != "POST":
        return redirect("edition_steps", edition_id=edition.id)

    pipeline_state = EditionPipeline.objects.filter(edition=edition).first()

    def _redirect_after_step() -> str:
        fresh_state = EditionPipeline.objects.filter(edition=edition).first()
        target_lang = utils.normalize_lang(
            request.POST.get("target_language")
            or request.POST.get("md_language")
            or (fresh_state.frontmatter_language if fresh_state and fresh_state.frontmatter_locked else "")
            or (fresh_state.md_language if fresh_state and fresh_state.md_language else "")
            or (fresh_state.translation_language if fresh_state and fresh_state.translation_language else "")
            or edition.language.code
        )
        try:
            redirect_edition = _edition_for_language(edition, target_lang)
        except Exception:
            redirect_edition = edition
        return _edition_steps_redirect_url(
            redirect_edition,
            frontmatter_lang=(
                fresh_state.frontmatter_language
                if fresh_state and fresh_state.frontmatter_language
                else target_lang
            ),
            frontmatter_locked=bool(fresh_state and fresh_state.frontmatter_locked),
        )

    def _target_lang() -> str:
        if pipeline_state and pipeline_state.frontmatter_locked and pipeline_state.frontmatter_language:
            return pipeline_state.frontmatter_language
        if pipeline_state and pipeline_state.md_language:
            return pipeline_state.md_language
        return edition.language.code

    def _target_edition():
        target_lang = utils.normalize_lang(_target_lang())
        if target_lang == utils.normalize_lang(edition.language.code):
            return edition
        return EditorialEdition.objects.get(work__code=book_code, language__code=target_lang)

    def _assert_block_04_ready(target_edition) -> None:
        build_dir = paths.edition_build_dir(target_edition)
        final_md_exists = (build_dir / "BOOK.MD_FINAL").exists()
        template = BookEditionTemplate.objects.filter(
            book_code=target_edition.work.code,
            language=utils.normalize_lang(_target_lang()),
        ).first()
        if not _editorial_required_fields_ready(template):
            raise ValueError(
                "Bloco 04 bloqueado: o Bloco 03 precisa ter frontmatter obrigatorio pronto para o idioma selecionado."
            )
        if not final_md_exists:
            raise ValueError(
                "Bloco 04 bloqueado: gere BOOK.MD_FINAL antes da finalizacao."
            )

    try:
        if step == "raw":
            core_edition = _processing_base_edition(edition)
            uploaded = request.FILES.get("raw_file")
            if not uploaded:
                raise ValueError("No raw file uploaded.")
            dest_path = _raw_upload_path(core_edition, uploaded.name)
            with dest_path.open("wb+") as dest:
                for chunk in uploaded.chunks():
                    dest.write(chunk)
            core_edition.raw_source_path = str(dest_path)
            core_edition.save(update_fields=["raw_source_path", "updated_at"])

            texts, _ = EditionText.objects.get_or_create(edition=core_edition)
            texts.raw_path = str(dest_path)
            texts.save(update_fields=["raw_path", "updated_at"])

            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=core_edition)
            pipeline_state.current_stage = PipelineStage.RAW
            pipeline_state.raw_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()
            messages.success(request, f"RAW saved: {dest_path}")

        elif step == "normalize":
            core_edition = _processing_base_edition(edition)
            out_path, normalized_source = _ensure_normalized_v2_for_heading_cleaner(core_edition)

            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=core_edition)
            if pipeline_state.raw_at is None:
                pipeline_state.raw_at = timezone.now()
            pipeline_state.current_stage = PipelineStage.NORMALIZED
            pipeline_state.normalized_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()

            messages.success(request, f"Normalize OK: {out_path} ({normalized_source})")

        elif step == "heading_cleaner":
            core_edition = _processing_base_edition(edition)
            _ensure_normalized_v2_for_heading_cleaner(core_edition)
            _invalidate_split_01_after_heading_cleaner(core_edition)
            agent_name = (request.POST.get("agent_name") or "MechanicalHeadingCleaner").strip()
            result = heading_cleaner.run_heading_cleaner(
                core_edition,
                agent_name=agent_name,
            )

            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=core_edition)
            pipeline_state.current_stage = PipelineStage.NORMALIZED
            pipeline_state.chunked_at = None
            pipeline_state.last_log = ""
            pipeline_state.save()

            messages.success(
                request,
                f"HeadingCleaner OK ({result['engine']}): {result['clean_path']}",
            )
            messages.info(request, f"Report: {result['report_path']}")

        elif step == "split":
            core_edition = _processing_base_edition(edition)
            count = editorial_split.run_split_struct(core_edition)
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=core_edition)
            pipeline_state.current_stage = PipelineStage.SPLIT
            pipeline_state.split_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()
            messages.success(request, f"Split struct OK: {count} units")

        elif step == "chunk":
            core_edition = _processing_base_edition(edition)
            clean_path = heading_cleaner.clean_path_for_book_code(_edition_codes(core_edition)[0])
            if not clean_path.exists():
                raise ValueError("Prerequisito: heading_cleaner/clean.txt.")
            count = editorial_split.run_split_01(core_edition)
            invalidated = _invalidate_downstream_pipeline_outputs(core_edition)
            book_id = _parse_book_id(book_code)
            chunks_dir = Path("data/chunks") / f"book_{book_id:04d}" / "split_01"
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=core_edition)
            pipeline_state.current_stage = PipelineStage.CHUNKED
            pipeline_state.chunked_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()
            messages.success(request, f"Chunks OK: {count}")
            if any(invalidated.values()):
                messages.info(
                    request,
                    "Downstream invalidados apos rechunk: "
                    f"translated_dirs={invalidated['translated_dirs']}, "
                    f"translated_files={invalidated['translated_files']}, "
                    f"build_files={invalidated['build_files']}, "
                    f"edition_core_files={invalidated['edition_core_files']}"
                )

        elif step == "split_by_chapter":
            split_step = next(
                (s for s in build_pipeline01_steps(edition, pipeline_state) if s.get("key") == "split_by_chapter"),
                None,
            )
            if not (split_step and bool(split_step.get("can_run"))):
                raise ValueError(
                    (split_step or {}).get("block_reason")
                    or "Prerequisito para Split by Chapter: merge_translate.txt canônico."
                )

            target_edition = _edition_for_language(
                edition,
                utils.normalize_lang(
                    (pipeline_state.translation_language if pipeline_state else None) or edition.language.code
                ),
            )
            requested_parts = _recommended_split_parts_for_translate_variant(
                getattr(pipeline_state, "translation_language", None) if pipeline_state else None
            )
            result = chapter_agent.run_split_by_chapter(
                target_edition,
                parts_per_chapter=requested_parts,
            )
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=target_edition)
            pipeline_state.last_log = (
                f"{timezone.now().isoformat()} :: SPLIT_BY_CHAPTER :: "
                f"chapters={result['chapter_count']} parts={result['part_count']} "
                f"parts_per_chapter={requested_parts}"
            )
            pipeline_state.save(update_fields=["last_log"])
            messages.success(
                request,
                f"Split by chapter OK: {result['chapter_count']} capitulos / {result['part_count']} partes",
            )
            messages.info(request, f"Merge base: {result['merge_translate_path']}")
            messages.info(request, f"Manifest: {result['manifest_path']}")

        elif step == "translate":
            target_language = _normalize_translate_variant(request.POST.get("target_language") or language)
            if core_docker.should_run_in_docker(step, target_language):
                result = core_docker.run_docker_core_step(
                    project_root=Path(settings.BASE_DIR).parent,
                    edition_id=edition.id,
                    step=step,
                    language=_translate_base_language(target_language),
                    target_language=target_language,
                )
                messages.success(request, f"Translate OK (docker:{_translate_variant_label(target_language)})")
                if result.stdout.strip():
                    messages.info(request, result.stdout.strip())
            else:
                result_payload = execute_language_isolated_core_step(
                    edition_id=edition.id,
                    step=step,
                    target_language=target_language,
                )
                messages.success(request, result_payload["success"])
                for item in result_payload.get("info", []):
                    messages.info(request, item)

        elif step == "refine":
            target_language = utils.normalize_lang(edition.language.code)
            refine_profile = _normalized_refine_profile_for_language(
                request.POST.get("refine_profile") or (pipeline_state.refine_profile if pipeline_state else ""),
                target_language,
            )
            if core_docker.should_run_in_docker(step, target_language):
                result = core_docker.run_docker_core_step(
                    project_root=Path(settings.BASE_DIR).parent,
                    edition_id=edition.id,
                    step=step,
                    language=target_language,
                    refine_profile=refine_profile,
                )
                messages.success(request, f"Refine OK (docker:{target_language})")
                if result.stdout.strip():
                    messages.info(request, result.stdout.strip())
            else:
                result_payload = execute_language_isolated_core_step(
                    edition_id=edition.id,
                    step=step,
                    refine_profile=refine_profile,
                )
                messages.success(request, result_payload["success"])
                for item in result_payload.get("info", []):
                    messages.info(request, item)

        elif step == "merge_refine":
            target_edition = edition
            target_language = utils.normalize_lang(target_edition.language.code)
            refine_profile_cfg = _refine_profile_config(
                getattr(pipeline_state, "refine_profile", "") if pipeline_state is not None else ""
            )
            refine_source_dir, _refine_source_label = _resolve_refine_source_dir(target_edition, target_language)
            refine_dir = _resolve_refine_output_dir(
                refine_source_dir,
                refine_profile=getattr(pipeline_state, "refine_profile", "") if pipeline_state is not None else "",
                target_language=target_language,
            )
            _validate_runtime_chunk_outputs(refine_source_dir, refine_dir, "MergeRefine")
            merge_refine_build = paths.merge_refine_path(target_edition)
            merge_refine_clean = (
                Path(settings.BASE_DIR).parent
                / "data"
                / "translated"
                / target_edition.work.code
                / "merge_refine_clean.txt"
            )
            canonical_merge.write_canonical_merge(
                refine_source_dir,
                refine_dir,
                merge_refine_build,
                book_code=target_edition.work.code,
                language=target_language,
            )
            canonical_merge.write_canonical_merge(
                refine_source_dir,
                refine_dir,
                merge_refine_clean,
                book_code=target_edition.work.code,
                language=target_language,
            )

            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=target_edition)
            pipeline_state.current_stage = PipelineStage.MERGED
            pipeline_state.merged_at = timezone.now()
            pipeline_state.last_log = f"{timezone.now().isoformat()} :: MERGE_REFINE :: {merge_refine_clean}"
            pipeline_state.save(update_fields=["current_stage", "merged_at", "last_log"])
            messages.success(request, f"MergeRefine OK: {merge_refine_clean}")

        elif step == "qa_refine":
            target_edition = edition
            result = refine_qa.run_refine_qa(target_edition)
            summary = result.get("summary") or {}
            if result.get("pass"):
                messages.success(
                    request,
                    (
                        f"Refine QA PASS (critical={summary.get('critical', 0)}, "
                        f"major={summary.get('major', 0)}, minor={summary.get('minor', 0)})"
                    ),
                )
            else:
                messages.warning(
                    request,
                    (
                        f"Refine QA FAIL (critical={summary.get('critical', 0)}, "
                        f"major={summary.get('major', 0)}, minor={summary.get('minor', 0)})"
                    ),
                )
            messages.info(request, f"Refine QA JSON: {result['json_path']}")
            messages.info(request, f"Refine QA MD: {result['md_path']}")

        elif step == "preflight":
            target_edition = edition
            target_lang = utils.normalize_lang(_target_lang())
            frontmatter_template = BookEditionTemplate.objects.filter(
                book_code=target_edition.work.code,
                language=target_lang,
            ).first()
            can_run, block_reason = _preflight_gate(target_edition, frontmatter_template)
            if not can_run:
                raise ValueError(block_reason or "Prerequisito para Pre-flight: conclua o Bloco 03.")
            result = preflight.run_preflight(target_edition)
            messages.success(
                request,
                (
                    "Pre-flight OK: "
                    f"{result['verdict']} "
                    f"(critical={result['critical_count']}, medium={result['medium_count']}, light={result['light_count']})"
                ),
            )
            messages.info(request, f"Pre-flight source: {result['source_path']}")
            messages.info(request, f"Pre-flight JSON: {result['json_path']}")
            messages.info(request, f"Pre-flight MD: {result['md_path']}")

        elif step == "polish":
            target_language = utils.normalize_lang(edition.language.code)
            if core_docker.should_run_in_docker(step, target_language):
                result = core_docker.run_docker_core_step(
                    project_root=Path(settings.BASE_DIR).parent,
                    edition_id=edition.id,
                    step=step,
                    language=target_language,
                )
                messages.success(request, f"Polish OK (docker:{target_language})")
                if result.stdout.strip():
                    messages.info(request, result.stdout.strip())
            else:
                result_payload = execute_language_isolated_core_step(
                    edition_id=edition.id,
                    step=step,
                )
                messages.success(request, result_payload["success"])

        elif step == "txt_to_md":
            md_language = request.POST.get("md_language") or None
            target_language = md_language or edition.language.code
            target_edition = _edition_for_language(edition, target_language)
            result = md_transform.run_txt_to_md(target_edition, language_override=md_language)
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=target_edition)
            pipeline_state.md_language = md_language or ""
            pipeline_state.save(update_fields=["md_language"])
            items = result.get("items") or []
            if len(items) > 1:
                outputs = ", ".join(f"{item['language']}: {item['path']}" for item in items)
                msg = f"TXT to MD OK: {outputs}"
            else:
                msg = f"TXT to MD OK: {result['path']}"
                if result.get("path_pre_qa"):
                    msg = f"{msg} (PRE_QA: {result['path_pre_qa']})"
            messages.success(request, msg)

        elif step == "txt_to_miolo":
            result = miolo_transform.run_txt_to_miolo_from_reference(edition)
            items = result.get("items") or []
            if len(items) > 1:
                outputs = ", ".join(f"{item['language']}: {item['path']}" for item in items)
                msg = f"TXT to Miolo OK: {outputs}"
            else:
                msg = f"TXT to Miolo OK: {result['path']}"
                if result.get("path"):
                    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
                    pipeline_state.current_stage = PipelineStage.MIOLO_MD
                    pipeline_state.miolo_md_at = timezone.now()
                    pipeline_state.last_log = ""
                    pipeline_state.save()
            messages.success(request, msg)

        elif step == "qa":
            messages.warning(request, "QA suspenso no momento.")

        elif step == "approve_md":
            pipeline_state = EditionPipeline.objects.filter(edition=edition).first()
            locked_lang = None
            if pipeline_state and pipeline_state.frontmatter_locked:
                locked_lang = pipeline_state.frontmatter_language
            target_lang = (
                locked_lang
                or (pipeline_state.md_language if pipeline_state else None)
                or edition.language.code
            )

            if target_lang and target_lang != edition.language.code:
                build_dir = paths.edition_build_dir_for_language(book_code, target_lang)
                candidates = [
                    build_dir / f"BOOK.QA.{target_lang}.md",
                    build_dir / f"BOOK.PRE_EDITION.{target_lang}.md",
                    build_dir / f"BOOK.PRE_QA.{target_lang}.md",
                    build_dir / "BOOK.QA.md",
                    build_dir / "BOOK.PRE_EDITION.md",
                    build_dir / "BOOK.PRE_QA.md",
                ]
                source_path = next((p for p in candidates if p.exists()), None)
                if not source_path:
                    raise FileNotFoundError(
                        f"No QA/PRE file found for language {target_lang} to approve."
                    )
                final_path = build_dir / "BOOK.MD_FINAL"
                final_path.parent.mkdir(parents=True, exist_ok=True)
                final_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
                result = {"path": str(final_path), "source": str(source_path)}
            else:
                result = md_quality.approve_md_final(edition)
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
            pipeline_state.current_stage = PipelineStage.FINAL_MD
            pipeline_state.final_md_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save(update_fields=["current_stage", "final_md_at", "last_log"])
            messages.success(
                request,
                f"MD final saved: {result['path']}",
            )

        elif step == "build":
            target_edition = _target_edition()
            _assert_block_04_ready(target_edition)
            kdp_mode.build_frontmatter_files(target_edition, storage.frontmatter_dir())
            merged_path = kdp_mode.build_merged_kdp_source(target_edition)
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=target_edition)
            pipeline_state.miolo_md_at = timezone.now()
            pipeline_state.editorial_changed = False
            pipeline_state.build_outdated = False
            pipeline_state.last_built_at = timezone.now()
            if not pipeline_state.final_md_at and pipeline_state.current_stage != PipelineStage.DONE:
                pipeline_state.current_stage = PipelineStage.MIOLO_MD
            pipeline_state.last_log = ""
            pipeline_state.save(update_fields=["miolo_md_at", "editorial_changed", "build_outdated", "last_built_at", "current_stage", "last_log"])
            build_path = kdp_mode.builds_dir(target_edition) / "BOOK.BUILD.MD"
            history = _record_build_history(
                target_edition,
                language_code=utils.normalize_lang(_target_lang()),
                build_path=build_path,
                notes="Build final gerado a partir do bloco 04.",
            )
            result = {"path": str(build_path), "merged": str(merged_path), "build_version": history.build_version}
            messages.success(request, f"Build OK: {result['path']}")

        elif step == "export_epub":
            target_edition = _target_edition()
            _assert_block_04_ready(target_edition)
            epub_output = kdp_mode.build_epub_for_edition(target_edition)
            result = {"path": str(epub_output)}
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=target_edition)
            if pipeline_state.miolo_md_at is None:
                pipeline_state.miolo_md_at = timezone.now()
            if pipeline_state.final_md_at:
                pipeline_state.current_stage = PipelineStage.DONE
            pipeline_state.editorial_changed = False
            pipeline_state.build_outdated = False
            pipeline_state.last_built_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save(update_fields=["miolo_md_at", "editorial_changed", "build_outdated", "last_built_at", "current_stage", "last_log"])
            _record_build_history(
                target_edition,
                language_code=utils.normalize_lang(_target_lang()),
                epub_path=epub_output,
                notes="EPUB gerado a partir do bloco 04.",
            )
            messages.success(request, f"EPUB OK: {result['path']}")

        elif step == "export_pdf":
            target_edition = _target_edition()
            _assert_block_04_ready(target_edition)
            pdf_output = kdp_mode.build_print_pdf_for_edition(target_edition)
            result = {"path": str(pdf_output)}
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=target_edition)
            pipeline_state.editorial_changed = False
            pipeline_state.build_outdated = False
            pipeline_state.last_built_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save(update_fields=["editorial_changed", "build_outdated", "last_built_at", "last_log"])
            _record_build_history(
                target_edition,
                language_code=utils.normalize_lang(_target_lang()),
                pdf_path=pdf_output,
                notes="PDF gerado a partir do bloco 04.",
            )
            messages.success(request, f"PDF OK: {result['path']}")

        elif step == "epubcheck":
            target_edition = _target_edition()
            _assert_block_04_ready(target_edition)
            result = {"path": str(kdp_mode.run_epubcheck_for_edition(target_edition))}
            messages.success(request, f"epubcheck OK: {result['path']}")

        elif step == "gaiden":
            target_lang = _target_lang()
            target_edition = _target_edition()
            _assert_block_04_ready(target_edition)

            build_dir = (
                paths.edition_build_dir_for_language(book_code, target_lang)
                if target_lang != edition.language.code
                else paths.edition_build_dir(edition)
            )

            md_final = kdp_mode.builds_dir(target_edition) / "BOOK.MD_FINAL"
            if not md_final.exists():
                alt_md_final = build_dir / "BOOK.MD_FINAL"
                if alt_md_final.exists():
                    md_final = alt_md_final
                else:
                    raise FileNotFoundError("No BOOK.MD_FINAL found. Run QA + Approve first.")

            build_md = kdp_mode.builds_dir(target_edition) / "BOOK.BUILD.MD"
            if not build_md.exists():
                build_result = build_book.run_build(
                    edition,
                    language_override=target_lang if target_lang != edition.language.code else None,
                )
                messages.info(request, f"Build auto (legacy): {build_result['path']}")

            epub_result = export_book.run_export_epub(
                edition,
                language_override=target_lang if target_lang != edition.language.code else None,
            )
            messages.success(request, f"EPUB legacy OK: {epub_result['path']}")

            result = kdp_mode.gaiden_build_full_book(target_edition)
            messages.success(request, f"Gaiden full OK: EPUB={result['epub']} PDF={result['pdf']}")

            export_user = (
                request.user.username if getattr(request, "user", None) and request.user.is_authenticated else "system"
            )
            manifest = book_manifest.build_manifest(
                edition,
                target_edition,
                export_user=export_user,
                epubcheck_status="unknown",
            )
            manifest_path = book_manifest.write_manifest(target_edition, manifest)
            messages.success(request, f"Manifest saved: {manifest_path}")

        else:
            messages.error(request, f"Unknown step: {step}")

    except Exception as exc:
        messages.error(request, f"Step {step} failed: {exc}")
        pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
        pipeline_state.last_log = str(exc)
        pipeline_state.save()

    return redirect(_redirect_after_step())


def build_book_md(request, book_code, language):
    if request.method != "POST":
        return redirect("book_edition_list")

    edition = get_object_or_404(
        EditorialEdition,
        work__code=book_code,
        language__code=language,
    )

    call_command(
        "build_book_text",
        book_code=edition.work.code,
        language=edition.language.code,
    )

    return redirect("preview_book_md", book_code=book_code, language=language)


def preview_book_md(request, book_code, language):
    build_dir = paths.edition_build_dir_for_language(book_code, language)
    candidates = [
        get_book_md_path(book_code, language),
        build_dir / "BOOK.BUILD.MD",
        build_dir / "BOOK.MD_FINAL",
        build_dir / f"BOOK.PRE_EDITION.{language}.md",
        build_dir / f"BOOK.PRE_QA.{language}.md",
        build_dir / "BOOK.PRE_EDITION.md",
        build_dir / "BOOK.PRE_QA.md",
    ]
    path = next((p for p in candidates if p.exists()), None)
    if not path:
        raise Http404("Markdown file not found for preview.")

    content = path.read_text(encoding="utf-8")
    context = {
        "book_code": book_code,
        "language": language,
        "md_path": str(path),
        "content": content,
    }
    return render(request, "pipeline/preview_md.html", context)


def preview_pre_edition_md(request, book_code, language):
    build_dir = paths.edition_build_dir_for_language(book_code, language)
    candidates = [
        build_dir / f"BOOK.PRE_EDITION.{language}.md",
        build_dir / f"BOOK.PRE_QA.{language}.md",
        build_dir / "BOOK.PRE_EDITION.md",
        build_dir / "BOOK.PRE_QA.md",
    ]
    path = next((p for p in candidates if p.exists()), None)
    if not path:
        raise Http404("Markdown file not found for preview.")

    content = path.read_text(encoding="utf-8")
    context = {
        "book_code": book_code,
        "language": language,
        "md_path": str(path),
        "content": content,
    }
    return render(request, "pipeline/preview_md.html", context)


def preview_merge_translate(request, edition_id: int):
    edition = get_object_or_404(EditorialEdition, id=edition_id)
    pipeline_state = EditionPipeline.objects.filter(edition=edition).first()
    book_code, language = _edition_codes(edition)

    target_language = _normalize_translate_variant(
        (pipeline_state.translation_language if pipeline_state else None) or language
    )
    target_base = _translate_base_language(target_language)
    out_dir_path = _runtime_translate_dir_for_edition(edition, target_language)
    merged_path = _detect_merged_path(out_dir_path)
    if not merged_path:
        build_candidates = [
            paths.edition_build_dir_for_language(book_code, target_base) / f"merge_translate_{target_base}.txt",
            paths.edition_build_dir_for_language(book_code, target_base) / "merge_translate.txt",
        ]
        merged_path = next((path for path in build_candidates if path.exists()), None)
    if not merged_path:
        raise Http404("Merged translation file not found.")

    content = merged_path.read_text(encoding="utf-8")
    context = {
        "book_code": book_code,
        "language": target_base,
        "md_path": str(merged_path),
        "content": content,
    }
    return render(request, "pipeline/preview_md.html", context)


def save_merge_translate_preview(request, edition_id: int):
    if request.method != "POST":
        return redirect("edition_steps", edition_id=edition_id)

    edition = get_object_or_404(EditorialEdition, id=edition_id)
    pipeline_state = EditionPipeline.objects.filter(edition=edition).first()
    book_code, language = _edition_codes(edition)

    target_language = _normalize_translate_variant(
        (pipeline_state.translation_language if pipeline_state else None) or language
    )
    target_base = _translate_base_language(target_language)
    out_dir_path = _runtime_translate_dir_for_edition(edition, target_language)
    merged_path = _detect_merged_path(out_dir_path)
    if not merged_path:
        build_candidates = [
            paths.edition_build_dir_for_language(book_code, target_base) / f"merge_translate_{target_base}.txt",
            paths.edition_build_dir_for_language(book_code, target_base) / "merge_translate.txt",
        ]
        merged_path = next((path for path in build_candidates if path.exists()), None)
    if not merged_path:
        messages.error(request, "Merged translation file not found.")
        return redirect("edition_steps", edition_id=edition_id)

    content = merged_path.read_text(encoding="utf-8")
    build_dir = paths.edition_build_dir_for_language(book_code, target_base)
    build_dir.mkdir(parents=True, exist_ok=True)
    saved_path = build_dir / f"merge_translate_{target_base}.txt"
    saved_path.write_text(content, encoding="utf-8")

    TextSnapshot.objects.create(
        edition=edition,
        language=target_base,
        stage="merge_translate_preview",
        source_path=str(merged_path),
        content=content,
    )

    PipelineJob.objects.create(
        book_code=book_code,
        book_title=edition.work.title,
        language=target_base,
        stage="translate",
        status="SUCCESS",
        filepath=str(saved_path),
        message="Saved preview merge translate to build dir.",
    )

    messages.success(request, f"Arquivo salvo: {saved_path}")
    return redirect("edition_steps", edition_id=edition_id)


def preview_miolo_md(request, book_code, language):
    edition = get_object_or_404(
        EditorialEdition,
        work__code=book_code,
        language__code=language,
    )
    candidates = [
        paths.miolo_md_path_for_language(book_code, language),
        paths.miolo_md_path(edition),
    ]
    path = next((p for p in candidates if p.exists()), None)
    if not path:
        raise Http404("Miolo markdown file not found for preview.")

    content = path.read_text(encoding="utf-8")
    context = {
        "book_code": book_code,
        "language": language,
        "md_path": str(path),
        "content": content,
    }
    return render(request, "pipeline/preview_md.html", context)
