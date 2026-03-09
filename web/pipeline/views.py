import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from datetime import datetime
import re
import zipfile

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
    Edition as EditorialEdition,
    EditionPipeline,
    EditionText,
    Language,
    PipelineStage,
    Seal,
    Work,
)
from editorial import kdp_mode

from .models import (
    BookEditionTemplate,
    PipelineJob,
    TextSnapshot,
    get_book_md_path,
)
try:
    from .models import PipelineRun, PipelineRunItem
except ImportError:
    PipelineRun = None
    PipelineRunItem = None
from .forms import BookEditionTemplateForm
from .services import (
    book_manifest,
    build_book,
    chapter_chunks,
    editorial_split,
    export_book,
    html_preprod,
    heading_cleaner,
    legacy_merges,
    md_quality,
    md_transform,
    miolo_transform,
    paths,
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

_HTML_STAGE_ORDER = {
    PipelineStage.RAW: 10,
    STAGE_HTML_UPLOADED: 20,
    STAGE_HTML_PREPROD_READY: 30,
    STAGE_MD_SOURCE_READY: 40,
    PipelineStage.NORMALIZED: 50,
}

logger = logging.getLogger(__name__)


def _normalize_source_format(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in SOURCE_FORMAT_ALLOWED:
        return SOURCE_FORMAT_TXT
    return normalized


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


def _write_uploaded_file_atomic(dest_path: Path, uploaded_file) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".upload_", dir=str(dest_path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as temp_fp:
            for chunk in uploaded_file.chunks():
                temp_fp.write(chunk)
        os.replace(temp_path, dest_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _language_defaults(language_code: str) -> dict[str, str]:
    labels = {
        "en": "English",
        "ptbr": "Portugues (Brasil)",
        "es": "Espanol",
        "de": "Deutsch",
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


def book_edition_list(request):
    editions = (
        EditorialEdition.objects.select_related("work", "language", "seal")
        .order_by("work__code", "language__code")
    )
    return render(request, "pipeline/book_edition_list.html", {"editions": editions})


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
        try:
            selected_source_format = _validate_ingest_v1_request(canonical_post_data, request.FILES)
        except ValidationError as exc:
            return HttpResponseBadRequest(str(exc))
    else:
        selected_source_format = _normalize_source_format(
            template.text_source_mode if template else SOURCE_FORMAT_TXT
        )

    if request.method == "POST":
        form = BookEditionTemplateForm(canonical_post_data, request.FILES, instance=template)
        if form.is_valid():
            source_file = form.cleaned_data["source_file"]
            try:
                with transaction.atomic():
                    template = form.save(commit=False)
                    template.text_source_mode = selected_source_format
                    template.save()
                    book_code = template.book_code
                    language = utils.normalize_lang(template.language)
                    root = Path(settings.BASE_DIR).parent
                    saved_paths: dict[str, str] = {}

                    try:
                        edition, edition_created = _ensure_editorial_edition(template)
                    except ValidationError as exc:
                        form.add_error(None, str(exc))
                        raise

                    ext = ".html" if selected_source_format == SOURCE_FORMAT_HTML else ".txt"
                    raw_path = root / "data" / "raw" / book_code / f"{book_code}_{language}_raw{ext}"
                    _write_uploaded_file_atomic(raw_path, source_file)
                    saved_paths["raw_path"] = str(raw_path)

                    cover_file = form.cleaned_data.get("cover_file")
                    if cover_file:
                        cover_dir = root / "data" / "covers" / book_code / language
                        cover_dir.mkdir(parents=True, exist_ok=True)
                        ext = (Path(cover_file.name).suffix or ".jpg").lower()
                        cover_path = cover_dir / f"cover{ext}"
                        _write_uploaded_file_atomic(cover_path, cover_file)
                        try:
                            template.cover_filepath = str(cover_path.relative_to(root))
                        except ValueError:
                            template.cover_filepath = str(cover_path)
                        template.save(update_fields=["cover_filepath"])
                        saved_paths["cover_path"] = template.cover_filepath

                    images_zip = form.cleaned_data.get("images_zip")
                    if images_zip:
                        images_dir = root / "data" / "images" / book_code / language
                        if images_dir.exists():
                            shutil.rmtree(images_dir)
                        images_dir.mkdir(parents=True, exist_ok=True)
                        with zipfile.ZipFile(images_zip) as archive:
                            archive.extractall(images_dir)
                        try:
                            template.images_dir = str(images_dir.relative_to(root))
                        except ValueError:
                            template.images_dir = str(images_dir)
                        template.save(update_fields=["images_dir"])
                        saved_paths["images_dir"] = template.images_dir

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
                    edition.raw_source_path = saved_paths["raw_path"]
                    if "cover_path" in saved_paths:
                        edition.cover_filepath = saved_paths["cover_path"]
                    edition.save()

                    texts, _ = EditionText.objects.get_or_create(edition=edition)
                    texts.raw_path = saved_paths["raw_path"]
                    texts.save(update_fields=["raw_path", "updated_at"])

                    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
                    pipeline_state.raw_at = timezone.now()
                    pipeline_state.current_stage = (
                        STAGE_HTML_UPLOADED
                        if template.text_source_mode == SOURCE_FORMAT_HTML
                        else STAGE_TXT_UPLOADED
                    )
                    pipeline_state.save(update_fields=["raw_at", "current_stage"])

                    kdp_mode.build_frontmatter_files(edition, Path("data") / "frontmatter")

                logger.info(
                    "pipeline_ingest_v1",
                    extra={
                        "book_code": book_code,
                        "language": language,
                        "source_format": template.text_source_mode,
                        "stage": pipeline_state.current_stage,
                        "redirect": (
                            reverse("pipeline_html_dashboard", kwargs={"edition_id": edition.id})
                            if template.text_source_mode == SOURCE_FORMAT_HTML
                            else reverse("edition_steps", kwargs={"edition_id": edition.id})
                        ),
                        "raw_path": saved_paths["raw_path"],
                        "result": "ok",
                    },
                )
            except ValidationError:
                incoming_file = request.FILES.get("source_file")
                if incoming_file:
                    messages.warning(
                        request,
                        (
                            f"Arquivo recebido ({incoming_file.name}), mas houve erro ao criar a edicao no DB. "
                            "Corrija os erros e tente novamente."
                        ),
                    )
                return render(
                    request,
                    "pipeline/book_edition_form.html",
                    {
                        "form": form,
                        "source_format": selected_source_format,
                    },
                )
            except Exception as exc:
                logger.exception(
                    "pipeline_ingest_v1_failed",
                    extra={
                        "book_code": canonical_post_data.get("book_code"),
                        "language": canonical_post_data.get("language"),
                        "source_format": selected_source_format,
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
                return render(
                    request,
                    "pipeline/book_edition_form.html",
                    {
                        "form": form,
                        "source_format": selected_source_format,
                    },
                )

            if edition_created:
                messages.info(request, f"Edicao editorial criada automaticamente para {book_code} [{language}].")
            messages.success(request, f"Cadastro salvo e arquivos prontos para {book_code} [{language}].")
            if template.text_source_mode == SOURCE_FORMAT_HTML:
                return redirect("pipeline_html_dashboard", edition_id=edition.id)
            return redirect("edition_steps", edition_id=edition.id)
        incoming_file = request.FILES.get("source_file")
        if incoming_file:
            messages.warning(
                request,
                (
                    f"Arquivo recebido ({incoming_file.name}), mas o cadastro teve erro em outros campos. "
                    "Veja os erros abaixo."
                ),
            )
        elif template is None:
            messages.error(
                request,
                "Nenhum arquivo chegou ao backend nesta submissao. Selecione o arquivo novamente e envie.",
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

    return render(
        request,
        "pipeline/book_edition_form.html",
        {
            "form": form,
            "source_format": selected_source_format,
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




def _edition_codes(edition) -> tuple[str, str]:
    return edition.work.code, edition.language.code


def _global_core_edition(edition) -> EditorialEdition:
    if utils.normalize_lang(edition.language.code) == "en":
        return edition
    try:
        return EditorialEdition.objects.get(work__code=edition.work.code, language__code="en")
    except EditorialEdition.DoesNotExist as exc:
        raise ValueError(f"Edicao EN nao encontrada para {edition.work.code}.") from exc


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
    data_dir = Path(settings.BASE_DIR).parent / "data"
    raw_base_dir = data_dir / "raw"
    dest_dir = raw_base_dir / book_code
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(uploaded_name).suffix or ".txt"
    return dest_dir / f"{book_code}_{language}_raw{ext}"


def _resolve_project_path(path_value: str | Path) -> Path:
    candidate = Path(path_value)
    if candidate.is_absolute():
        return candidate
    return Path(settings.BASE_DIR).parent / candidate


def _normalized_v2_path(book_code: str, language: str) -> Path:
    lang = utils.normalize_lang(language)
    return (
        Path(settings.BASE_DIR).parent
        / "data"
        / "normalized"
        / f"{book_code}_{lang}_v2.txt"
    )


def _split_01_dir(book_code: str) -> Path:
    book_id = _parse_book_id(book_code)
    if book_id is None:
        raise ValueError("book_code must be like book_0001 to resolve split_01 dir.")
    return Path(settings.BASE_DIR).parent / "data" / "chunks" / f"book_{book_id:04d}" / "split_01"


def _heading_cleaner_dir(book_code: str) -> Path:
    book_id = _parse_book_id(book_code)
    if book_id is None:
        raise ValueError("book_code must be like book_0001 to resolve heading_cleaner dir.")
    return Path(settings.BASE_DIR).parent / "data" / "chunks" / f"book_{book_id:04d}" / "heading_cleaner"


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

    source_md_path = html_preprod.artifact_paths(book_code, language)["md_source"]
    if source_md_path.exists():
        normalized_text = source_md_path.read_text(encoding="utf-8")
        out_path.write_text(normalized_text, encoding="utf-8")
        texts.normalized_text = normalized_text
        texts.normalized_path = str(out_path)
        texts.save(update_fields=["normalized_text", "normalized_path", "updated_at"])
        return out_path, "html_source_md"

    from gaiden import ingest, normalize as gaiden_normalize

    raw_path_str = (texts.raw_path or "").strip() or (core_edition.raw_source_path or "").strip()
    if not raw_path_str:
        raise FileNotFoundError("RAW file not found and source.md missing. Cannot prepare normalized_v2.")
    raw_path = _resolve_project_path(raw_path_str)
    if not raw_path.exists():
        raise FileNotFoundError(f"RAW path not found: {raw_path}")
    ext = raw_path.suffix.lstrip(".")
    text = ingest.extract_text_from_file(raw_path, ext)
    if not text:
        raise ValueError("Could not extract text from RAW file to prepare normalized_v2.")
    normalized_text = gaiden_normalize.normalize_text_v2(text)
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


def _edition_steps_redirect_url(edition) -> str:
    book_code, language = _edition_codes(edition)
    template = BookEditionTemplate.objects.filter(
        book_code=book_code,
        language=utils.normalize_lang(language),
    ).first()
    url = reverse("edition_steps", kwargs={"edition_id": edition.id})
    if _source_format_from_template(template) == SOURCE_FORMAT_HTML:
        return f"{url}?allow_html_to_common=1"
    return url


def _rel_project_path(path: Path) -> str:
    root = Path(settings.BASE_DIR).parent
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def build_pipeline01_steps(edition, pipeline_state: EditionPipeline | None = None) -> list[dict]:
    root = Path(settings.BASE_DIR).parent
    core_edition = _global_core_edition(edition)
    core_book_code, core_lang = _edition_codes(core_edition)
    core_lang = utils.normalize_lang(core_lang)

    source_md = html_preprod.artifact_paths(core_book_code, core_lang)["md_source"]
    normalized_path = _normalized_v2_path(core_book_code, core_lang)
    split_dir = _split_01_dir(core_book_code)
    split_chunks = sorted(split_dir.glob("*.txt")) if split_dir.exists() else []
    heading_dir = _heading_cleaner_dir(core_book_code)
    heading_clean_path = heading_cleaner.clean_path_for_book_code(core_book_code)
    heading_report_path = heading_cleaner.report_path_for_book_code(core_book_code)

    target_lang = utils.normalize_lang(
        (pipeline_state.translation_language if pipeline_state and pipeline_state.translation_language else "")
        or edition.language.code
    )
    try:
        target_edition = _edition_for_language(edition, target_lang)
    except ValueError:
        target_edition = edition
        target_lang = utils.normalize_lang(edition.language.code)

    contract_path: Path | None = None
    contract_exists = False
    contract_error = ""
    try:
        contract_path = _select_contract_path(target_lang)
        contract_exists = contract_path.exists()
    except ValueError as exc:
        contract_error = str(exc)

    translate_dir: Path | None = None
    translate_outputs_count = 0
    translate_merge_path = paths.merge_translate_path(target_edition)
    try:
        translate_dir = _runtime_translate_dir_for_edition(target_edition, target_lang)
        if translate_dir.exists():
            translate_outputs_count = len(list(translate_dir.glob("*.txt")))
    except Exception:
        translate_dir = None
    translate_done = translate_merge_path.exists() or translate_outputs_count > 0

    refine_dir = translate_dir / "return_aldebaran" if translate_dir else None
    refine_outputs_count = 0
    if refine_dir and refine_dir.exists():
        refine_outputs_count = len(list(refine_dir.glob("*.txt")))
    refine_merge_path = paths.merge_refine_path(target_edition)
    refine_done = refine_merge_path.exists() or refine_outputs_count > 0

    merge_refine_clean_path = root / "data" / "translated" / core_book_code / "merge_refine_clean.txt"
    merge_refine_done = merge_refine_clean_path.exists()

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
                f"Translate contract: {_rel_project_path(contract_path)}"
                if contract_path
                else f"Translate contract: {contract_error or 'nao resolvido'}"
            ),
        }
    )

    step_defs.append(
        {
            "n": 5,
            "key": "refine",
            "title": "Refine (Aldebaran)",
            "run_url": reverse("pipeline_refine_run", kwargs={"edition_id": edition.id}),
            "button_label": "Rodar Refine",
            "can_run": translate_done,
            "done": refine_done,
            "block_reason": "Prerequisito: outputs de translate." if not translate_done else "",
            "outputs": [
                _rel_project_path(refine_dir / "*.txt") if refine_dir else "data/translated/<book>/<lang_variant>/return_aldebaran/*.txt",
                _rel_project_path(refine_merge_path),
            ],
            "notes": "Agent: Aldebaran",
        }
    )

    step_defs.append(
        {
            "n": 6,
            "key": "merge_refine",
            "title": "Merge/Finalize",
            "run_url": reverse("pipeline_merge_refine_run", kwargs={"edition_id": edition.id}),
            "button_label": "Rodar MergeRefine",
            "can_run": refine_done,
            "done": merge_refine_done,
            "block_reason": "Prerequisito: outputs de refine." if not refine_done else "",
            "outputs": [
                _rel_project_path(refine_merge_path),
                _rel_project_path(merge_refine_clean_path),
            ],
            "notes": "Gera merge_refine_clean.txt canônico do Pipeline 01.",
        }
    )

    return step_defs


def _select_contract_path(language: str) -> Path:
    mapping = {
        "en": "gaiden/contracts/en_modern_2025.json",
        "es": "gaiden/contracts/en_es_2025.json",
        "ptbr": "gaiden/contracts/en_ptbr_2025.json",
        "de": "gaiden/contracts/en_de_krimi_2025.json",
    }
    rel = mapping.get(utils.normalize_lang(language))
    if not rel:
        raise ValueError(f"No translate contract for language={language}")
    return Path(settings.BASE_DIR).parent / rel


def _select_refine_contract(language: str) -> Path:
    mapping = {
        "en": "gaiden/contracts/refine/en_refine_2025.json",
        "es": "gaiden/contracts/refine/es_refine_2025.json",
        "ptbr": "gaiden/contracts/refine/ptbr_refine_2025.json",
    }
    rel = mapping.get(utils.normalize_lang(language))
    if not rel:
        raise ValueError(f"No refine contract for language={language}")
    return Path(settings.BASE_DIR).parent / rel


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
        else f"{utils.normalize_lang(target_language)}_2025"
    )
    return Path("data") / "translated" / book_token / str(variant)


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


def _harden_translate_contract(payload: dict) -> dict:
    system_prompt = payload.get("system_prompt") or payload.get("system") or ""
    user_prompt = payload.get("user_prompt") or payload.get("user") or "{text}"

    system_rules = (
        "CRITICAL OUTPUT RULES:\n"
        "- Output only the translated literary passage.\n"
        "- Do not add titles, headings, introductions, notes, summaries, bullet lists, numbered lists, analysis, commentary, or explanations.\n"
        "- Do not mention the prompt, the source text, copyright, safety policies, or your own translation choices.\n"
        "- Do not wrap the answer in quotes, code fences, markdown, or labels.\n"
        "- Preserve the passage as continuous narrative prose."
    )
    user_rules = (
        "Return only the final translated passage.\n"
        "No comments.\n"
        "No explanatory text.\n"
        "No summaries.\n"
        "No headings.\n"
        "No lists.\n"
        "No notes before or after the passage."
    )

    payload["system_prompt"] = _append_prompt_block(system_prompt, system_rules)
    payload["user_prompt"] = _append_prompt_block(user_prompt, user_rules)
    return payload


def _build_runtime_translate_contract(edition, target_language: str) -> tuple[Path, str]:
    book_code, _language = _edition_codes(edition)
    base_contract_path = _select_contract_path(target_language)
    payload = json.loads(base_contract_path.read_text(encoding="utf-8"))
    payload = _harden_translate_contract(payload)

    chunk_dir, input_glob, source_label = _translate_source_chunks(book_code)
    out_dir = _runtime_translate_out_dir(book_code, target_language, payload)
    if not out_dir.is_absolute():
        out_dir = Path(settings.BASE_DIR).parent / out_dir

    payload["chunk_dir"] = str(chunk_dir)
    payload["input_glob"] = input_glob
    payload["out_dir"] = str(out_dir)
    payload["target_language"] = utils.normalize_lang(target_language)

    if not isinstance(payload.get("output"), dict):
        payload["output"] = {}
    if utils.normalize_lang(target_language) == "ptbr":
        payload["output"]["language"] = "pt-br"
    else:
        payload["output"]["language"] = utils.normalize_lang(target_language)

    runtime_contract_path = (
        Path(settings.BASE_DIR).parent
        / "data"
        / "editions"
        / str(edition.id)
        / "core"
        / f"contract_translate_{utils.normalize_lang(target_language)}.json"
    )
    runtime_contract_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_contract_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return runtime_contract_path, source_label


def _runtime_translate_dir_for_edition(edition, target_language: str) -> Path:
    book_code, _language = _edition_codes(edition)
    payload = json.loads(_select_contract_path(target_language).read_text(encoding="utf-8"))
    out_dir = _runtime_translate_out_dir(book_code, target_language, payload)
    if out_dir.is_absolute():
        return out_dir
    return Path(settings.BASE_DIR).parent / out_dir


def _build_runtime_refine_contract(edition, target_language: str) -> tuple[Path, Path, Path]:
    payload = json.loads(_select_refine_contract(target_language).read_text(encoding="utf-8"))
    source_dir = _runtime_translate_dir_for_edition(edition, target_language)
    if not source_dir.exists():
        raise FileNotFoundError(f"Translate chunks not found for refine: {source_dir}. Run Translate first.")

    refine_input_dir = (
        Path(settings.BASE_DIR).parent
        / "data"
        / "editions"
        / str(edition.id)
        / "core"
        / f"refine_input_{utils.normalize_lang(target_language)}"
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

    out_dir = source_dir / "return_aldebaran"
    payload["chunk_dir"] = str(refine_input_dir)
    payload["out_dir"] = str(out_dir)
    payload["target_language"] = utils.normalize_lang(target_language)
    if not isinstance(payload.get("output"), dict):
        payload["output"] = {}
    payload["output"]["language"] = utils.normalize_lang(target_language)

    runtime_contract_path = (
        Path(settings.BASE_DIR).parent
        / "data"
        / "editions"
        / str(edition.id)
        / "core"
        / f"contract_refine_{utils.normalize_lang(target_language)}.json"
    )
    runtime_contract_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_contract_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return runtime_contract_path, refine_input_dir, out_dir


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
    data_dir = Path(settings.BASE_DIR).parent / "data"
    chunks_dir = data_dir / "chunks" / f"book_{book_id:04d}" / "split_01"
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
        return redirect("pipeline_html_dashboard", edition_id=edition.id)

    def _redirect_editorial():
        return redirect(f"{_edition_steps_redirect_url(edition)}#transformacao-editorial")

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
        if action == "upload_images_zip":
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
        if action == "upload_images_files":
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
        if action == "consolidate_images":
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
            target_language = utils.normalize_lang(request.POST.get("target_language") or language)
            try:
                target_edition = _edition_for_language(edition, target_language)
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("edition_steps", edition_id=edition.id)
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=target_edition)
            pipeline_state.translation_language = target_language
            pipeline_state.md_language = target_language
            pipeline_state.save(update_fields=["translation_language", "md_language"])
            messages.info(
                request,
                f"Idioma salvo ({target_language}). Refine ou Next Step.",
            )
            result = md_transform.run_txt_to_md(target_edition, language_override=target_language)
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
        if action == "apply_images":
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

    book_id = _parse_book_id(book_code)
    split_by_chapter_dir = None
    if book_id is not None:
        split_by_chapter_dir = (
            Path(settings.BASE_DIR).parent
            / "data"
            / "chunks"
            / f"book_{book_id:04d}"
            / "split_01_by_chapter"
        )

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
        pipeline_state.frontmatter_language
        or frontmatter_lang_param
        or language
    )
    if frontmatter_lang not in frontmatter_langs:
        frontmatter_lang = language
    if pipeline_state.frontmatter_locked and pipeline_state.frontmatter_language:
        frontmatter_lang = pipeline_state.frontmatter_language
        frontmatter_locked = True

    default_year = edition.edition_year or edition.work.year or datetime.now().year
    default_collab = (
        edition.main_contributor.name if edition.main_contributor else edition.work.author.name
    )
    frontmatter_template, created = BookEditionTemplate.objects.get_or_create(
        book_code=book_code,
        language=frontmatter_lang,
        defaults={
            "title": edition.work.title,
            "subtitle": "",
            "author_name": edition.work.author.name,
            "publication_year": default_year,
            "imprint_name": edition.seal.name,
            "collection_name": "",
            "collaborator_name": default_collab,
            "collaborator_pseudonym": "",
            "collaborator_roles": "",
        },
    )
    if created:
        frontmatter_template.save()

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
    md_source_map = {
        lang: _resolve_md_source_path(lang)
        for lang in ("en", "es", "ptbr", "de")
    }
    md_source_map_json = json.dumps(md_source_map)
    translate_contract_map: dict[str, str] = {}
    project_root = Path(settings.BASE_DIR).parent
    for lang in ("en", "es", "ptbr", "de"):
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

    context = {
        "edition": edition,
        "edition_steps_action_url": _edition_steps_redirect_url(edition),
        "source_format": source_format,
        "status": {
            "raw": _status(bool(raw_path)),
            "normalize": _status(bool(pipeline_state.normalized_at)),
            "heading_cleaner": _status(heading_cleaner_done),
            "split": _status(bool(pipeline_state.split_at)),
            "split_by_chapter": _status(bool(split_by_chapter_dir and split_by_chapter_dir.exists())),
            "translate": _status(bool(pipeline_state.translated_at)),
            "refine": _status(bool(pipeline_state.refined_at)),
            "qa_refine": refine_qa_status,
            "polish": _status(bool(pipeline_state.polished_at)),
        },
        "raw_path": raw_path,
        "raw_name": raw_name,
        "translate_language": pipeline_state.translation_language or language,
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
        "book_code": book_code,
        "language": language,
        "frontmatter_lang": frontmatter_lang,
        "frontmatter_lang_choices": BookEditionTemplate.LANG_CHOICES,
        "frontmatter_template": frontmatter_template,
        "frontmatter_preview": frontmatter_template.frontispiece_rendered,
        "copyright_preview": frontmatter_template.copyright_rendered,
        "frontmatter_locked": frontmatter_locked,
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
        "images_dir_path": str(images_dir) if images_dir.exists() else None,
        "images_count": images_count,
        "images_consolidated_dir_path": str(consolidated_images_dir) if consolidated_images_dir.exists() else None,
        "images_consolidated_count": consolidated_images_count,
        "images_consolidated_map_path": str(consolidated_images_map) if consolidated_images_map.exists() else None,
        "matrix_runs": matrix_runs,
    }

    return render(request, "pipeline/edition_steps.html", context)


def pipeline_heading_cleaner_run(request, edition_id: int):
    edition = get_object_or_404(EditorialEdition, id=edition_id)
    if request.method != "POST":
        return redirect(_edition_steps_redirect_url(edition))

    core_edition = _global_core_edition(edition)
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


def run_edition_step(request, edition_id: int, step: str):
    edition = get_object_or_404(EditorialEdition, id=edition_id)
    book_code, language = _edition_codes(edition)

    if request.method != "POST":
        return redirect("edition_steps", edition_id=edition.id)

    pipeline_state = EditionPipeline.objects.filter(edition=edition).first()

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

    try:
        if step == "raw":
            core_edition = _global_core_edition(edition)
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
            core_edition = _global_core_edition(edition)
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
            core_edition = _global_core_edition(edition)
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
            core_edition = _global_core_edition(edition)
            count = editorial_split.run_split_struct(core_edition)
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=core_edition)
            pipeline_state.current_stage = PipelineStage.SPLIT
            pipeline_state.split_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()
            messages.success(request, f"Split struct OK: {count} units")

        elif step == "chunk":
            core_edition = _global_core_edition(edition)
            clean_path = heading_cleaner.clean_path_for_book_code(_edition_codes(core_edition)[0])
            if not clean_path.exists():
                raise ValueError("Prerequisito: heading_cleaner/clean.txt.")
            count = editorial_split.run_split_01(core_edition)
            book_id = _parse_book_id(book_code)
            chunks_dir = Path("data/chunks") / f"book_{book_id:04d}" / "split_01"
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=core_edition)
            pipeline_state.current_stage = PipelineStage.CHUNKED
            pipeline_state.chunked_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()
            messages.success(request, f"Chunks OK: {count}")

        elif step == "split_by_chapter":
            result = chapter_chunks.run_split_by_chapter(edition)
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
            pipeline_state.current_stage = PipelineStage.SPLIT
            pipeline_state.split_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()
            messages.success(request, f"Split by chapter OK: {result['path']}")

        elif step == "translate":
            from gaiden.translate import run_translate_with_contract

            translate_step = next(
                (s for s in build_pipeline01_steps(edition, pipeline_state) if s.get("key") == "translate"),
                None,
            )
            if not (translate_step and bool(translate_step.get("can_run"))):
                reason = (translate_step or {}).get("block_reason") or (
                    "Prerequisito para Translate: rode HeadingCleaner e depois refaca split_01."
                )
                raise ValueError(
                    reason
                )

            target_language = utils.normalize_lang(request.POST.get("target_language") or language)
            target_edition = _edition_for_language(edition, target_language)
            book_id_for_run = _parse_book_id(_edition_codes(target_edition)[0])
            if book_id_for_run is not None:
                os.environ["GAIDEN_BOOK_ID"] = str(book_id_for_run)
            stage_policy.POLICY.assert_stage_allowed(target_edition, "translate")
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=target_edition)
            if target_language == "de":
                core_edition = _global_core_edition(edition)
                core_state = EditionPipeline.objects.filter(edition=core_edition).first()
                core_path_value = (core_state.core_last_txt_path if core_state else "") or ""
                if not core_path_value:
                    messages.error(request, "Salve o Core antes de traduzir.")
                    return redirect("edition_steps", edition_id=edition.id)
                core_path = _resolve_core_path(core_path_value)
                if not core_path.exists():
                    messages.error(request, "Core salvo nao encontrado. Re-salve o Core.")
                    return redirect("edition_steps", edition_id=edition.id)

                core_chunks_dir = (
                    Path(settings.BASE_DIR).parent
                    / "data"
                    / "editions"
                    / str(target_edition.id)
                    / "core"
                    / "chunks_de"
                )
                core_chunks_dir.mkdir(parents=True, exist_ok=True)
                (core_chunks_dir / "0001.txt").write_text(
                    core_path.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                contract_path = _select_contract_path(target_language)
                contract_payload = json.loads(contract_path.read_text(encoding="utf-8"))
                contract_payload["chunk_dir"] = str(core_chunks_dir)
                contract_payload["out_dir"] = str(
                    Path(settings.BASE_DIR).parent
                    / "data"
                    / "editions"
                    / str(target_edition.id)
                    / "translate"
                    / "de_krimi"
                )
                core_contract_path = (
                    Path(settings.BASE_DIR).parent
                    / "data"
                    / "editions"
                    / str(target_edition.id)
                    / "core"
                    / "contract_de.json"
                )
                core_contract_path.parent.mkdir(parents=True, exist_ok=True)
                core_contract_path.write_text(
                    json.dumps(contract_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                run_translate_with_contract(core_contract_path)
                out_dir_path = _resolve_contract_out_dir(core_contract_path, target_edition)
            else:
                runtime_contract_path, source_label = _build_runtime_translate_contract(
                    target_edition,
                    target_language,
                )
                run_translate_with_contract(runtime_contract_path)
                out_dir_path = _resolve_contract_out_dir(runtime_contract_path, target_edition)
                messages.info(request, f"Translate source: {source_label}")

            merged_path = _detect_merged_path(out_dir_path)
            if not merged_path:
                raise FileNotFoundError(f"Merged translation not found in {out_dir_path}")
            build_path = _copy_merge_to_build(
                target_edition,
                merged_path,
                paths.merge_translate_path(target_edition),
            )
            pipeline_state.current_stage = PipelineStage.TRANSLATED
            pipeline_state.translation_language = target_language
            pipeline_state.translated_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()
            messages.success(request, "Translate OK")

        elif step == "refine":
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
            source_dir = _runtime_translate_dir_for_edition(target_edition, target_language)
            if not source_dir.exists():
                raise FileNotFoundError(
                    f"Translate chunks not found for refine: {source_dir}. Run Translate first."
                )

            try:
                from gaiden.tools.aldebaran_refine_return import run_aldebaran_refine_return

                out_dir_path = source_dir / "return_aldebaran"
                result = run_aldebaran_refine_return(
                    chunk_dir=source_dir,
                    out_dir=out_dir_path,
                    merge_name=f"merge_refine_{target_language}.txt",
                    agent_name="Aldebaran",
                )
                merged_path = Path(result["merge_path"])
            except ModuleNotFoundError:
                from gaiden.translate import run_translate_with_contract

                runtime_contract_path, refine_input_dir, out_dir_path = _build_runtime_refine_contract(
                    target_edition, target_language
                )
                run_translate_with_contract(runtime_contract_path)
                merged_candidates = [
                    out_dir_path / f"merge_refine_{target_language}.txt",
                    out_dir_path / "merge_refine.txt",
                    out_dir_path / "merged_return_aldebaran.txt",
                    out_dir_path / "merged.txt",
                ]
                merged_candidates.extend(sorted(out_dir_path.glob("merged_*.txt")))
                merged_path = next((p for p in merged_candidates if p.exists()), None)
                if merged_path is None:
                    raise FileNotFoundError(f"Refine merged output not found in {out_dir_path}")
                result = {
                    "agent_name": "Aldebaran (contract-fallback)",
                    "source_dir": str(refine_input_dir),
                    "report_path": str(runtime_contract_path),
                    "merge_path": str(merged_path),
                }

            build_path = _copy_merge_to_build(
                target_edition,
                merged_path,
                paths.merge_refine_path(target_edition),
            )
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=target_edition)
            pipeline_state.current_stage = PipelineStage.REFINED
            pipeline_state.refined_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()
            messages.success(request, f"Refine OK ({result['agent_name']})")
            messages.info(request, f"Refine source: {result['source_dir']}")
            messages.info(request, f"Refine report: {result['report_path']}")

        elif step == "merge_refine":
            target_edition = edition
            target_language = utils.normalize_lang(target_edition.language.code)
            merge_refine_build = paths.merge_refine_path(target_edition)
            if not merge_refine_build.exists():
                refine_dir = _runtime_translate_dir_for_edition(target_edition, target_language) / "return_aldebaran"
                candidates = [
                    refine_dir / f"merge_refine_{target_language}.txt",
                    refine_dir / "merge_refine.txt",
                    refine_dir / f"merged_refined_{target_language}_2025.txt",
                    refine_dir / f"merged_refined_{target_language}.txt",
                ]
                merge_source = next((p for p in candidates if p.exists()), None)
                if not merge_source:
                    raise FileNotFoundError(
                        f"Refine output not found in {refine_dir}. Run Refine (Aldebaran) first."
                    )
                _copy_merge_to_build(target_edition, merge_source, merge_refine_build)

            merge_refine_clean = (
                Path(settings.BASE_DIR).parent
                / "data"
                / "translated"
                / target_edition.work.code
                / "merge_refine_clean.txt"
            )
            merge_refine_clean.parent.mkdir(parents=True, exist_ok=True)
            merge_refine_clean.write_text(merge_refine_build.read_text(encoding="utf-8"), encoding="utf-8")

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

        elif step == "polish":
            from gaiden.polish_en_2025 import run_polish_en_2025

            target_edition = edition
            stage_policy.POLICY.assert_stage_allowed(target_edition, "polish")
            book_id = _parse_book_id(book_code)
            if book_id is None:
                raise ValueError("book_code must be like book_0001 to polish.")
            if utils.normalize_lang(target_edition.language.code) != "en":
                raise ValueError("Polish is only available for English.")

            run_polish_en_2025(book_id=book_id, lang_key="en_modern_2025")
            out_path = Path(f"data/chunks/book_{book_id:04d}/refine_en_01/merged_polished_en_2025.txt")
            build_path = _copy_merge_to_build(
                target_edition,
                out_path,
                paths.merge_polish_path(target_edition),
            )
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=target_edition)
            pipeline_state.current_stage = PipelineStage.POLISHED
            pipeline_state.polished_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()
            messages.success(request, "Polish OK")

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
            messages.success(
                request,
                f"MD final saved: {result['path']}",
            )

        elif step == "build":
            target_edition = _target_edition()
            kdp_mode.build_frontmatter_files(target_edition, Path("data") / "frontmatter")
            merged_path = kdp_mode.build_merged_kdp_source(target_edition)
            result = {"path": str(kdp_mode.builds_dir(target_edition) / "BOOK.BUILD.MD"), "merged": str(merged_path)}
            messages.success(request, f"Build OK: {result['path']}")

        elif step == "export_epub":
            target_edition = _target_edition()
            result = {"path": str(kdp_mode.build_epub_for_edition(target_edition))}
            messages.success(request, f"EPUB OK: {result['path']}")

        elif step == "export_pdf":
            target_edition = _target_edition()
            result = {"path": str(kdp_mode.build_print_pdf_for_edition(target_edition))}
            messages.success(request, f"PDF OK: {result['path']}")

        elif step == "epubcheck":
            target_edition = _target_edition()
            result = {"path": str(kdp_mode.run_epubcheck_for_edition(target_edition))}
            messages.success(request, f"epubcheck OK: {result['path']}")

        elif step == "gaiden":
            target_lang = _target_lang()
            target_edition = _target_edition()

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

    return redirect(_edition_steps_redirect_url(edition))


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

    target_language = utils.normalize_lang(
        (pipeline_state.translation_language if pipeline_state else None) or language
    )
    out_dir_path = _runtime_translate_dir_for_edition(edition, target_language)
    merged_path = _detect_merged_path(out_dir_path)
    if not merged_path:
        raise Http404("Merged translation file not found.")

    content = merged_path.read_text(encoding="utf-8")
    context = {
        "book_code": book_code,
        "language": target_language,
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

    target_language = utils.normalize_lang(
        (pipeline_state.translation_language if pipeline_state else None) or language
    )
    out_dir_path = _runtime_translate_dir_for_edition(edition, target_language)
    merged_path = _detect_merged_path(out_dir_path)
    if not merged_path:
        messages.error(request, "Merged translation file not found.")
        return redirect("edition_steps", edition_id=edition_id)

    content = merged_path.read_text(encoding="utf-8")
    build_dir = paths.edition_build_dir_for_language(book_code, target_language)
    build_dir.mkdir(parents=True, exist_ok=True)
    saved_path = build_dir / f"merge_translate_{target_language}.txt"
    saved_path.write_text(content, encoding="utf-8")

    TextSnapshot.objects.create(
        edition=edition,
        language=target_language,
        stage="merge_translate_preview",
        source_path=str(merged_path),
        content=content,
    )

    PipelineJob.objects.create(
        book_code=book_code,
        book_title=edition.work.title,
        language=target_language,
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
