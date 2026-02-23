import json
import logging
import os
import sys
import zipfile
from pathlib import Path
import shutil
from datetime import datetime
import re
import subprocess

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from editorial.models import (
    Edition as EditorialEdition,
    EditionPipeline,
    EditionText,
    PipelineStage,
    Language,
    Seal,
    Work,
    Contributor,
    ContributorRole,
)
from editorial import kdp_mode

from gaiden.contracts_v2.resolver import resolve_translate_contract_path
from gaiden.lang import normalize_lang_code
from gaiden.secrets_loader import require_openai_ready
from gaiden.tools.agent_translate_default import run_agent_translate
from gaiden.translate_engine_v1 import run_translate_safe
from gaiden.translate_mode_policy import apply_skip_policy
from gaiden.translate_artifacts import (
    active_pointer_filename,
    normalize_mode,
    resolve_active_or_latest,
)

from .models import (
    BookEditionTemplate,
    PipelineJob,
    PipelineRun,
    PipelineRunItem,
    PipelineRunState,
    TextSnapshot,
    get_book_md_path,
)
from .services import (
    book_manifest,
    build_book,
    canonical_index,
    chapter_chunks,
    export_book,
    fix_text,
    legacy_merges,
    md_quality,
    md_transform,
    miolo_transform,
    paths,
    image_pipeline,
    canonical,
    run_state_policy,
    stage_policy,
    utils,
)

RETURN_FLOW_CONTRACTS = {
    "en": "gaiden/contracts_v2/refine/return_flow_en_2026.json",
    "es": "gaiden/contracts_v2/refine/return_flow_es_2026.json",
    "ptbr": "gaiden/contracts_v2/refine/return_flow_ptbr_2026.json",
    "de": "gaiden/contracts_v2/refine/return_flow_de_2026.json",
    "fr": "gaiden/contracts_v2/refine/return_flow_fr_2026.json",
    "it": "gaiden/contracts_v2/refine/return_flow_it_2026.json",
}

PROJECT_LANGS = [
    {"code": "en", "label": "EN"},
    {"code": "de", "label": "DE"},
    {"code": "fr", "label": "FR"},
    {"code": "it", "label": "IT"},
    {"code": "es", "label": "ES"},
    {"code": "ptbr", "label": "PT-BR"},
]
PROJECT_LANG_CODES = {l["code"] for l in PROJECT_LANGS}
PROJECT_SOURCE_FORMATS = ["TXT", "MD"]

TRANSLATE_TARGETS = [
    {"code": "en", "label": "EN"},
    {"code": "de", "label": "DE"},
    {"code": "fr", "label": "FR"},
    {"code": "es", "label": "ES"},
    {"code": "ptbr", "label": "PT-BR"},
    {"code": "it", "label": "IT"},
]
TRANSLATE_TARGET_CODES = {t["code"] for t in TRANSLATE_TARGETS}
TRANSLATE_LEGACY_BOOKS = {"book_0001", "book_0002"}

MAX_RAW_UPLOAD_BYTES = 50 * 1024 * 1024
logger = logging.getLogger(__name__)

def _parse_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in {"1", "true", "on", "yes", "y"}:
        return True
    if raw in {"0", "false", "off", "no", "n", ""}:
        return False
    return default

def pipeline_dashboard(request):
    pipelines = EditionPipeline.objects.select_related("edition__work", "edition__language").order_by("edition__work__code")
    return render(request, "pipeline/dashboard.html", {"pipelines": pipelines})


def pipeline_project_dashboard(request):
    data_dir = _project_root() / "data"
    book_filter = (request.GET.get("book_code") or "").strip()
    works = Work.objects.order_by("code")
    if book_filter:
        works = works.filter(code=book_filter)
    languages = [
        {"code": "en", "label": "EN"},
        {"code": "de", "label": "DE"},
        {"code": "fr", "label": "FR"},
        {"code": "it", "label": "IT"},
        {"code": "es", "label": "ES"},
        {"code": "ptbr", "label": "PT-BR"},
    ]

    latest_map: dict[tuple[str, str], PipelineRunItem] = {}
    for item in PipelineRunItem.objects.order_by("-id"):
        key = (item.book_code, utils.normalize_lang(item.lang))
        if key not in latest_map:
            latest_map[key] = item

    books = []
    for work in works:
        book_code = work.code
        book_id = _parse_book_id(book_code)

        langs = []
        for lang in languages:
            lang_code = utils.normalize_lang(lang["code"])
            fs_lang = _fs_lang_dir(lang_code)
            raw_status = _project_raw_status(book_code, lang_code, work.source_format)
            normalized_path = data_dir / "normalized" / book_code / fs_lang / f"{book_code}_{lang_code}_v2.txt"
            normalize_report = data_dir / "normalized" / book_code / fs_lang / "normalize_report.json"
            chunks_manifest = data_dir / "chunks" / book_code / fs_lang / "chunks_manifest.json"
            translated_path = None
            split_dir = None
            refine_path = None
            polish_path = None
            epub_exists = False
            if book_id is not None:
                lang_dir = _runner_lang_dir(lang_code)
                out_dir = data_dir / "translated" / f"book_{book_id:04d}" / lang_dir
                translated_path = _detect_merged_path(out_dir)
                split_dir = out_dir / "split_chapters_for_refine"
                build_dir = data_dir / "builds" / book_code / lang_code
                refine_path = build_dir / "merge_refine.txt"
                polish_path = build_dir / "merge_polish.txt"
                epub_exists = bool(list(build_dir.glob("*.epub"))) if build_dir.exists() else False

            normalize_status = "MISSING"
            normalize_badge = "bad"
            if normalized_path.exists():
                report = _read_json(normalize_report)
                if report and report.get("status") == "OK":
                    normalize_status = "OK"
                    normalize_badge = "ok"
                elif report and report.get("status") == "FAIL":
                    normalize_status = "FAIL"
                    normalize_badge = "bad"
                else:
                    normalize_status = "WARN"
                    normalize_badge = "warn"

            chunk_status = "MISSING"
            chunk_badge = "bad"
            if chunks_manifest.exists():
                manifest = _read_json(chunks_manifest)
                if manifest and manifest.get("check_ok") is True:
                    chunk_status = "OK"
                    chunk_badge = "ok"
                elif manifest and manifest.get("check_ok") is False:
                    chunk_status = "FAIL"
                    chunk_badge = "bad"
                else:
                    chunk_status = "WARN"
                    chunk_badge = "warn"

            latest = latest_map.get((book_code, lang_code))
            langs.append(
                {
                    "code": lang_code,
                    "label": lang["label"],
                    "raw_ok": raw_status.get("exists", False),
                    "raw_invalid": raw_status.get("invalid_state", False),
                    "raw_path": raw_status.get("path", ""),
                    "normalize_status": normalize_status,
                    "normalize_badge": normalize_badge,
                    "chunk_status": chunk_status,
                    "chunk_badge": chunk_badge,
                    "translated_ok": bool(translated_path and translated_path.exists()),
                    "split_ok": bool(split_dir and split_dir.exists()),
                    "refine_ok": bool(refine_path and refine_path.exists()),
                    "polish_ok": bool(polish_path and polish_path.exists()),
                    "epub_ok": epub_exists,
                    "latest_action": latest.run.action if latest else "",
                    "latest_status": latest.status if latest else "",
                }
            )

        books.append(
            {
                "code": book_code,
                "title": work.title,
                "langs": langs,
            }
        )

    context = {
        "books": books,
    }
    return render(request, "pipeline/project_dashboard.html", context)


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _project_lang_db_code(code: str) -> str:
    norm = utils.normalize_lang(code)
    if norm == "ptbr":
        return "pt-br"
    return norm


def _project_lang_dir(code: str) -> str:
    return _fs_lang_dir(code)


def _fs_lang_dir(code: str) -> str:
    return utils.normalize_lang(code)

def _raw_lang_upper(code: str) -> str:
    norm = utils.normalize_lang(code)
    return "PT-BR" if norm == "ptbr" else norm.upper()


def _raw_dir_candidates(book_code: str, lang_code: str) -> tuple[Path, Path]:
    base = _project_root() / "data" / "raw" / book_code
    lower_dir = base / _fs_lang_dir(lang_code)
    upper_dir = base / _raw_lang_upper(lang_code)
    return lower_dir, upper_dir


def _raw_source_candidates(book_code: str, lang_code: str) -> dict[str, Path]:
    lower_dir, upper_dir = _raw_dir_candidates(book_code, lang_code)
    return {
        "lower_txt": lower_dir / "source.txt",
        "lower_md": lower_dir / "source.md",
        "upper_txt": upper_dir / "source.txt",
        "upper_md": upper_dir / "source.md",
        "lower_dir": lower_dir,
        "upper_dir": upper_dir,
    }


def _project_raw_paths(book_code: str, lang_code: str) -> tuple[Path, Path]:
    candidates = _raw_source_candidates(book_code, lang_code)
    return candidates["lower_txt"], candidates["lower_md"]


def _project_raw_path(book_code: str, lang_code: str, source_format: str | None = None) -> Path:
    txt_path, md_path = _project_raw_paths(book_code, lang_code)
    if source_format:
        return txt_path if source_format.upper() == "TXT" else md_path
    return txt_path


def _project_raw_status(book_code: str, lang_code: str, source_format: str) -> dict:
    candidates = _raw_source_candidates(book_code, lang_code)
    lower_txt = candidates["lower_txt"]
    lower_md = candidates["lower_md"]
    upper_txt = candidates["upper_txt"]
    upper_md = candidates["upper_md"]

    lower_txt_exists = lower_txt.exists()
    lower_md_exists = lower_md.exists()
    upper_txt_exists = upper_txt.exists()
    upper_md_exists = upper_md.exists()

    if lower_txt_exists and lower_md_exists:
        return {
            "exists": True,
            "path": f"{lower_txt} | {lower_md}",
            "size": "-",
            "mtime": "-",
            "invalid_state": True,
        }

    if lower_txt_exists or lower_md_exists:
        selected = lower_txt if lower_txt_exists else lower_md
        stat = selected.stat()
        return {
            "exists": True,
            "path": str(selected),
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime),
        }

    if upper_txt_exists and upper_md_exists:
        return {
            "exists": True,
            "path": f"{upper_txt} | {upper_md}",
            "size": "-",
            "mtime": "-",
            "invalid_state": True,
        }

    if upper_txt_exists or upper_md_exists:
        selected = upper_txt if upper_txt_exists else upper_md
        stat = selected.stat()
        return {
            "exists": True,
            "path": str(selected),
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime),
        }

    path = _project_raw_path(book_code, lang_code, source_format)
    return {"exists": False, "path": str(path)}


def _project_validate_book_code(code: str) -> bool:
    return bool(re.match(r"^book_\\d{4}$", code))


def _project_get_language(code: str) -> Language:
    db_code = _project_lang_db_code(code)
    return Language.objects.get(code=db_code)


def _project_get_or_create_language(code: str) -> Language:
    db_code = _project_lang_db_code(code)
    obj, _ = Language.objects.get_or_create(
        code=db_code,
        defaults={
            "name": db_code.upper(),
            "native_name": db_code.upper(),
            "is_active": True,
        },
    )
    return obj


def normalize_book_code(raw: str) -> tuple[str | None, str | None]:
    if raw is None:
        raw = ""
    value = raw.strip().lower()
    if not value:
        return None, "book_code deve seguir o padrão book_#### (ex: book_0002)."

    value = value.replace("-", "_").replace(" ", "_")
    while "__" in value:
        value = value.replace("__", "_")

    digits = ""
    if re.fullmatch(r"\d+", value):
        digits = value
    else:
        match = re.match(r"^book_?(\d+)$", value)
        if not match:
            return None, "book_code deve seguir o padrão book_#### (ex: book_0002)."
        digits = match.group(1)

    if len(digits) > 4:
        return None, "book_code deve ter no máximo 4 dígitos (ex: book_0002)."

    try:
        number = int(digits)
    except ValueError:
        return None, "book_code deve seguir o padrão book_#### (ex: book_0002)."

    if number < 1 or number > 9999:
        return None, "book_code deve estar entre 0001 e 9999."

    return f"book_{number:04d}", None


def _normalize_preview_allowed_lang(raw: str | None) -> str | None:
    if not raw:
        return "en"
    code = utils.normalize_lang(raw)
    if code in PROJECT_LANG_CODES:
        return code
    return None


def _resolve_raw_source_any(book_code: str, lang_code: str) -> tuple[Path | None, str]:
    candidates = _raw_source_candidates(book_code, lang_code)
    lower_txt = candidates["lower_txt"]
    lower_md = candidates["lower_md"]
    upper_txt = candidates["upper_txt"]
    upper_md = candidates["upper_md"]

    lower_txt_exists = lower_txt.exists()
    lower_md_exists = lower_md.exists()
    upper_txt_exists = upper_txt.exists()
    upper_md_exists = upper_md.exists()

    if lower_txt_exists and lower_md_exists:
        return None, "INVALID"
    if lower_txt_exists:
        return lower_txt, "RAW"
    if lower_md_exists:
        return lower_md, "RAW"

    if upper_txt_exists and upper_md_exists:
        return None, "INVALID"
    if upper_txt_exists:
        return upper_txt, "RAW"
    if upper_md_exists:
        return upper_md, "RAW"

    return None, "MISSING"


def _resolve_normalize_preview_path(book_code: str, lang: str) -> tuple[Path | None, str]:
    data_dir = _project_root() / "data"
    lang_code = utils.normalize_lang(lang)
    lang_dir = _fs_lang_dir(lang_code)
    base_name_v2 = f"{book_code}_{lang_code}_v2.txt"

    preview = data_dir / "normalized" / book_code / lang_dir / "normalize_preview.txt"
    if preview.exists():
        return preview, "PREVIEW"

    normalized = data_dir / "normalized" / book_code / lang_dir / base_name_v2
    if normalized.exists():
        return normalized, "NORMALIZED"

    raw_path, raw_kind = _resolve_raw_source_any(book_code, lang_code)
    if raw_kind == "RAW":
        return raw_path, "RAW"
    if raw_kind == "INVALID":
        return None, "INVALID"
    return None, "MISSING"


def _read_head_lines(path: Path, max_lines: int = 40, max_bytes: int = 65536) -> list[str]:
    with path.open("rb") as f:
        raw = f.read(max_bytes)
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[:max_lines]


def _read_tail_lines(path: Path, max_lines: int = 40, max_bytes: int = 131072) -> list[str]:
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        read_size = min(size, max_bytes)
        f.seek(-read_size, os.SEEK_END)
        raw = f.read(read_size)
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[-max_lines:]


def _count_lines_if_small(path: Path, max_bytes: int = 262144) -> int | None:
    try:
        if path.stat().st_size > max_bytes:
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
        return len(text.splitlines())
    except Exception:
        return None


def _normalize_preview_signals(sample: str) -> dict:
    low = sample.lower()
    return {
        "has_project_gutenberg": "project gutenberg" in low,
        "has_gutenberg_license": "gutenberg license" in low,
        "has_gutenberg_url": "www.gutenberg.org" in low,
        "has_start_marker": "*** start of" in low or "start of the project gutenberg" in low,
        "has_end_marker": "*** end of" in low or "end of the project gutenberg" in low,
    }


def projects_list(request):
    works = Work.objects.order_by("code")
    rows = []
    for work in works:
        base_lang = work.original_language.code
        enabled_langs = work.enabled_languages or ["en", "de", "fr", "it", "es", "ptbr"]
        base_edition = EditorialEdition.objects.filter(work=work, language=work.original_language).first()
        imprint = work.publisher or (base_edition.seal.name if base_edition and base_edition.seal else "")
        raw_status = _project_raw_status(work.code, base_lang, work.source_format)
        last_update = base_edition.updated_at if base_edition else None
        rows.append(
            {
                "book_code": work.code,
                "title": work.title,
                "imprint": imprint,
                "base_language": base_lang,
                "enabled_languages": enabled_langs,
                "raw_present": raw_status["exists"],
                "last_update": last_update,
                "base_edition_id": base_edition.id if base_edition else None,
            }
        )

    context = {
        "rows": rows,
    }
    return render(request, "pipeline/project_list.html", context)


def projects_hub(request, book_code: str):
    work = get_object_or_404(Work, code=book_code)
    enabled_langs = work.enabled_languages or ["en", "de", "fr", "it", "es", "ptbr"]
    base_lang = work.original_language.code

    raw_rows = []
    data_dir = _project_root() / "data"
    for lang in enabled_langs:
        lang_code = utils.normalize_lang(lang)
        lang_dir = _fs_lang_dir(lang_code)
        edition = EditorialEdition.objects.filter(
            work=work,
            language__code=_project_lang_db_code(lang_code),
        ).first()
        normalized_path = data_dir / "normalized" / book_code / lang_dir / f"{book_code}_{lang_code}_v2.txt"
        normalize_report = data_dir / "normalized" / book_code / lang_dir / "normalize_report.json"
        chunks_manifest = data_dir / "chunks" / book_code / lang_dir / "chunks_manifest.json"

        normalize_status = "MISSING"
        normalize_badge = "bad"
        if normalized_path.exists():
            report = _read_json(normalize_report)
            if report and report.get("status") == "OK":
                normalize_status = "OK"
                normalize_badge = "ok"
            elif report and report.get("status") == "FAIL":
                normalize_status = "FAIL"
                normalize_badge = "bad"
            else:
                normalize_status = "WARN"
                normalize_badge = "warn"

        chunk_status = "MISSING"
        chunk_badge = "bad"
        if chunks_manifest.exists():
            manifest = _read_json(chunks_manifest)
            if manifest and manifest.get("check_ok") is True:
                chunk_status = "OK"
                chunk_badge = "ok"
            elif manifest and manifest.get("check_ok") is False:
                chunk_status = "FAIL"
                chunk_badge = "bad"
            else:
                chunk_status = "WARN"
                chunk_badge = "warn"

        raw_rows.append(
            {
                "code": lang_code,
                "label": _runner_lang_dir(lang),
                **_project_raw_status(book_code, lang, work.source_format),
                "normalize_status": normalize_status,
                "normalize_badge": normalize_badge,
                "chunk_status": chunk_status,
                "chunk_badge": chunk_badge,
                "edition_status": edition.status if edition else "",
                "raw_uploaded_name": edition.raw_upload.name if edition and edition.raw_upload else "",
                "raw_materialized_path": edition.raw_materialized_path if edition else "",
            }
        )

    base_edition = EditorialEdition.objects.filter(work=work, language=work.original_language).first()

    context = {
        "work": work,
        "base_language": base_lang,
        "enabled_languages": enabled_langs,
        "raw_rows": raw_rows,
        "base_edition_id": base_edition.id if base_edition else None,
    }
    return render(request, "pipeline/project_hub.html", context)


def projects_normalize_preview(request, book_code: str, language: str = "en"):
    canonical, error = normalize_book_code(book_code)
    if error:
        messages.error(request, "book_code inválido.")
        return redirect("projects_list")
    if canonical != book_code:
        if language:
            return redirect("projects_normalize_preview_lang", book_code=canonical, language=language)
        return redirect("projects_normalize_preview", book_code=canonical)

    lang_code = _normalize_preview_allowed_lang(language)
    if not lang_code:
        messages.error(request, "Idioma inválido.")
        return redirect("projects_hub", book_code=canonical)

    file_path, file_kind = _resolve_normalize_preview_path(canonical, lang_code)
    data_dir = _project_root() / "data"
    report_path = data_dir / "normalized" / canonical / _fs_lang_dir(lang_code) / "normalize_report.json"
    report = _read_json(report_path)
    if file_kind == "INVALID":
        context = {
            "book_code": canonical,
            "language": lang_code,
            "file_kind": "INVALID",
            "report": report,
            "report_path": str(report_path),
        }
        return render(request, "pipeline/normalize_preview.html", context)

    if not file_path:
        context = {
            "book_code": canonical,
            "language": lang_code,
            "file_kind": "MISSING",
            "report": report,
            "report_path": str(report_path),
        }
        return render(request, "pipeline/normalize_preview.html", context)

    head_lines = _read_head_lines(file_path)
    tail_lines = _read_tail_lines(file_path)
    total_lines = _count_lines_if_small(file_path)

    head_display = [(idx + 1, line) for idx, line in enumerate(head_lines)]
    if total_lines is not None:
        start_line = max(1, total_lines - len(tail_lines) + 1)
        tail_display = [(start_line + idx, line) for idx, line in enumerate(tail_lines)]
    else:
        tail_display = [("…", line) for line in tail_lines]

    sample = "\n".join(head_lines + tail_lines)
    signals = _normalize_preview_signals(sample)

    stat = file_path.stat()
    context = {
        "book_code": canonical,
        "language": lang_code,
        "file_kind": file_kind,
        "file_path": str(file_path),
        "file_size": stat.st_size,
        "file_mtime": datetime.fromtimestamp(stat.st_mtime),
        "head_lines": head_display,
        "tail_lines": tail_display,
        "signals": signals,
        "total_lines": total_lines,
        "report": report,
        "report_path": str(report_path),
    }
    return render(request, "pipeline/normalize_preview.html", context)


def projects_chunks_manifest(request, book_code: str, language: str = "en"):
    canonical, error = normalize_book_code(book_code)
    if error:
        raise Http404("book_code inválido.")
    lang_code = utils.normalize_lang(language)
    if lang_code not in PROJECT_LANG_CODES:
        raise Http404("Idioma inválido.")

    data_dir = _project_root() / "data"
    manifest_path = data_dir / "chunks" / canonical / lang_code / "chunks_manifest.json"
    if not manifest_path.exists():
        raise Http404("Manifest não encontrado.")

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        raise Http404("Manifest inválido.")

    return JsonResponse(payload)


def projects_new(request):
    if request.method == "POST":
        raw_book_code = request.POST.get("book_code") or ""
        book_code, book_code_error = normalize_book_code(raw_book_code)
        title = (request.POST.get("title") or "").strip()
        subtitle = (request.POST.get("subtitle") or "").strip()
        author = (request.POST.get("author") or "").strip()
        imprint = (request.POST.get("imprint") or "").strip()
        base_language = utils.normalize_lang(request.POST.get("base_language") or "en")
        enabled_languages = [utils.normalize_lang(v) for v in request.POST.getlist("enabled_languages")]
        source_format = (request.POST.get("source_format") or "TXT").upper()
        notes = (request.POST.get("notes") or "").strip()

        if book_code_error:
            messages.error(request, book_code_error)
            return redirect("projects_new")
        if Work.objects.filter(code=book_code).exists():
            messages.error(request, "book_code já existe.")
            return redirect("projects_new")
        if not title:
            messages.error(request, "Título é obrigatório.")
            return redirect("projects_new")
        if not imprint:
            messages.error(request, "Imprint/Selo é obrigatório.")
            return redirect("projects_new")
        if base_language not in PROJECT_LANG_CODES:
            messages.error(request, "Idioma base inválido.")
            return redirect("projects_new")
        if not enabled_languages:
            enabled_languages = [base_language]
        if not set(enabled_languages).issubset(PROJECT_LANG_CODES):
            messages.error(request, "Idiomas habilitados inválidos.")
            return redirect("projects_new")
        if base_language not in enabled_languages:
            enabled_languages = [base_language, *enabled_languages]
        enabled_languages = list(dict.fromkeys(enabled_languages))
        if source_format not in PROJECT_SOURCE_FORMATS:
            messages.error(request, "Formato de origem inválido.")
            return redirect("projects_new")

        if raw_book_code.strip().lower() != book_code:
            messages.info(request, f"book_code normalizado para: {book_code}")

        upload = request.FILES.get("raw_file")
        if not upload:
            messages.error(request, "Selecione um arquivo RAW para criar o projeto.")
            return redirect("projects_new")
        if upload.size > MAX_RAW_UPLOAD_BYTES:
            messages.error(request, "Arquivo RAW muito grande.")
            return redirect("projects_new")
        expected_ext = ".txt" if source_format == "TXT" else ".md"
        upload_ext = Path(upload.name).suffix.lower()
        if upload_ext not in {".txt", ".md"}:
            messages.error(request, "Formato inválido. Use .txt ou .md.")
            return redirect("projects_new")
        if upload_ext != expected_ext:
            messages.error(request, f"Formato esperado: {expected_ext}")
            return redirect("projects_new")

        base_lang_obj = _project_get_or_create_language(base_language)
        seal_obj, _ = Seal.objects.get_or_create(
            slug=imprint.lower(),
            defaults={"name": imprint, "is_active": True},
        )
        author_obj = None
        if author:
            author_obj, _ = Contributor.objects.get_or_create(
                name=author,
                defaults={"role": ContributorRole.AUTHOR},
            )
        else:
            author_obj, _ = Contributor.objects.get_or_create(
                name="Unknown",
                defaults={"role": ContributorRole.AUTHOR},
            )

        work = Work.objects.create(
            code=book_code,
            title=title,
            subtitle=subtitle,
            original_language=base_lang_obj,
            author=author_obj,
            publisher=imprint,
            enabled_languages=enabled_languages,
            source_format=source_format,
            notes=notes,
        )

        base_edition, _ = EditorialEdition.objects.get_or_create(
            work=work,
            language=base_lang_obj,
            seal=seal_obj,
            defaults={
                "title": title,
                "subtitle": subtitle,
                "author": author or work.author.name,
                "publisher": imprint,
                "imprint_name": imprint,
                "seal_name": seal_obj.name,
            },
        )

        base_edition.raw_upload = upload
        base_edition.status = EditorialEdition.STATUS_UPLOADED
        base_edition.book_id = book_code
        base_edition.lang = base_language
        base_edition.save(update_fields=["raw_upload", "status", "book_id", "lang", "updated_at"])
        text, _ = EditionText.objects.get_or_create(edition=base_edition)
        text.raw_path = ""
        text.save(update_fields=["raw_path", "updated_at"])

        messages.success(
            request,
            "Projeto criado e RAW enviado para storage. "
            "Use 'Materialize RAW' em Edition Steps para gerar data/raw/<book>/<lang>/source.*.",
        )
        return redirect("projects_hub", book_code=work.code)

    context = {
        "languages": PROJECT_LANGS,
        "source_formats": PROJECT_SOURCE_FORMATS,
    }
    return render(request, "pipeline/project_new_wizard.html", context)


def projects_edit(request, book_code: str):
    work = get_object_or_404(Work, code=book_code)
    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()
        subtitle = (request.POST.get("subtitle") or "").strip()
        author = (request.POST.get("author") or "").strip()
        imprint = (request.POST.get("imprint") or "").strip()
        base_language = utils.normalize_lang(request.POST.get("base_language") or work.original_language.code)
        enabled_languages = [utils.normalize_lang(v) for v in request.POST.getlist("enabled_languages")]
        source_format = (request.POST.get("source_format") or work.source_format or "TXT").upper()
        notes = (request.POST.get("notes") or "").strip()

        if not title:
            messages.error(request, "Título é obrigatório.")
            return redirect("projects_edit", book_code=book_code)
        if not imprint:
            messages.error(request, "Imprint/Selo é obrigatório.")
            return redirect("projects_edit", book_code=book_code)
        if base_language not in PROJECT_LANG_CODES:
            messages.error(request, "Idioma base inválido.")
            return redirect("projects_edit", book_code=book_code)
        if not enabled_languages:
            enabled_languages = [base_language]
        if not set(enabled_languages).issubset(PROJECT_LANG_CODES):
            messages.error(request, "Idiomas habilitados inválidos.")
            return redirect("projects_edit", book_code=book_code)
        if base_language not in enabled_languages:
            enabled_languages = [base_language, *enabled_languages]
        enabled_languages = list(dict.fromkeys(enabled_languages))
        if source_format not in PROJECT_SOURCE_FORMATS:
            messages.error(request, "Formato de origem inválido.")
            return redirect("projects_edit", book_code=book_code)

        base_lang_obj = _project_get_or_create_language(base_language)
        seal_obj, _ = Seal.objects.get_or_create(
            slug=imprint.lower(),
            defaults={"name": imprint, "is_active": True},
        )
        author_obj = work.author
        if author and author != work.author.name:
            author_obj, _ = Contributor.objects.get_or_create(
                name=author,
                defaults={"role": ContributorRole.AUTHOR},
            )

        work.title = title
        work.subtitle = subtitle
        work.original_language = base_lang_obj
        work.author = author_obj
        work.publisher = imprint
        work.enabled_languages = enabled_languages
        work.source_format = source_format
        work.notes = notes
        work.save()

        base_edition = EditorialEdition.objects.filter(work=work, language=base_lang_obj).first()
        if not base_edition:
            base_edition = EditorialEdition.objects.create(
                work=work,
                language=base_lang_obj,
                seal=seal_obj,
                title=title,
                subtitle=subtitle,
                author=author or work.author.name,
                publisher=imprint,
                imprint_name=imprint,
                seal_name=seal_obj.name,
            )
        else:
            base_edition.seal = seal_obj
            base_edition.title = title
            base_edition.subtitle = subtitle
            base_edition.author = author or work.author.name
            base_edition.publisher = imprint
            base_edition.imprint_name = imprint
            base_edition.seal_name = seal_obj.name
            base_edition.save(
                update_fields=[
                    "seal",
                    "title",
                    "subtitle",
                    "author",
                    "publisher",
                    "imprint_name",
                    "seal_name",
                    "updated_at",
                ]
            )

        messages.success(request, "Projeto atualizado.")
        return redirect("projects_edit", book_code=book_code)

    context = {
        "work": work,
        "languages": PROJECT_LANGS,
        "source_formats": PROJECT_SOURCE_FORMATS,
    }
    return render(request, "pipeline/project_edit.html", context)


def projects_upload_raw(request, book_code: str, language: str):
    work = get_object_or_404(Work, code=book_code)
    lang_code = utils.normalize_lang(language)
    if lang_code not in PROJECT_LANG_CODES:
        messages.error(request, "Idioma inválido.")
        return redirect("projects_hub", book_code=book_code)
    edition = EditorialEdition.objects.filter(
        work=work,
        language__code=_project_lang_db_code(lang_code),
    ).first()

    if request.method == "POST":
        upload = request.FILES.get("raw_file")
        if not upload:
            messages.error(request, "Selecione um arquivo.")
            return redirect("projects_upload_raw", book_code=book_code, language=language)
        if upload.size > MAX_RAW_UPLOAD_BYTES:
            messages.error(request, "Arquivo muito grande.")
            return redirect("projects_upload_raw", book_code=book_code, language=language)

        expected_ext = ".txt" if work.source_format.upper() == "TXT" else ".md"
        upload_ext = Path(upload.name).suffix.lower()
        if upload_ext not in {".txt", ".md"}:
            messages.error(request, "Formato inválido. Use .txt ou .md.")
            return redirect("projects_upload_raw", book_code=book_code, language=language)
        if upload_ext != expected_ext:
            messages.error(request, f"Formato esperado: {expected_ext}")
            return redirect("projects_upload_raw", book_code=book_code, language=language)

        lang_obj = _project_get_or_create_language(lang_code)
        seal_obj, _ = Seal.objects.get_or_create(
            slug=work.publisher.lower() if work.publisher else "mantaquest",
            defaults={"name": work.publisher or "MantaQuest", "is_active": True},
        )
        edition, _ = EditorialEdition.objects.get_or_create(
            work=work,
            language=lang_obj,
            seal=seal_obj,
        )
        edition.raw_upload = upload
        edition.status = EditorialEdition.STATUS_UPLOADED
        edition.book_id = book_code
        edition.lang = lang_code
        edition.save(update_fields=["raw_upload", "status", "book_id", "lang", "updated_at"])
        texts, _ = EditionText.objects.get_or_create(edition=edition)
        texts.raw_path = ""
        texts.save(update_fields=["raw_path", "updated_at"])

        messages.success(
            request,
            "RAW enviado para storage. "
            "Use 'Materialize RAW' em Edition Steps para gerar o arquivo canônico em data/raw/.",
        )
        return redirect("projects_upload_raw", book_code=book_code, language=language)

    raw_status = _project_raw_status(book_code, lang_code, work.source_format)
    context = {
        "work": work,
        "language": lang_code,
        "raw_status": raw_status,
        "expected_ext": "txt" if work.source_format.upper() == "TXT" else "md",
        "edition_status": edition.status if edition else "",
        "raw_upload_name": edition.raw_upload.name if edition and edition.raw_upload else "",
        "raw_materialized_path": edition.raw_materialized_path if edition else "",
        "raw_sha256": edition.raw_sha256 if edition else "",
    }
    return render(request, "pipeline/project_upload_raw.html", context)


def edition_steps_by_code(request, book_code: str, language: str):
    lang_code = _project_lang_db_code(language)
    edition = get_object_or_404(EditorialEdition, work__code=book_code, language__code=lang_code)
    return redirect("edition_steps", edition_id=edition.id)


def pipeline_jobs(request):
    pipelines = EditionPipeline.objects.select_related("edition__work", "edition__language").order_by("-id")
    return render(request, "pipeline/jobs.html", {"pipelines": pipelines})


def runner_matrix_view(request):
    works = Work.objects.order_by("code")
    selected_book_code = (request.GET.get("book_code") or "").strip()
    default_langs = None
    if selected_book_code:
        selected_work = Work.objects.filter(code=selected_book_code).first()
        if selected_work:
            default_langs = selected_work.enabled_languages or ["en", "de", "fr", "it", "es", "ptbr"]
    languages = [
        {"code": "en", "label": "EN"},
        {"code": "de", "label": "DE"},
        {"code": "fr", "label": "FR"},
        {"code": "it", "label": "IT"},
        {"code": "es", "label": "ES"},
        {"code": "ptbr", "label": "PT-BR"},
    ]

    run_id = request.GET.get("run_id")
    if run_id:
        run = get_object_or_404(PipelineRun, id=run_id)
    else:
        run = PipelineRun.objects.order_by("-created_at").first()
    items = run.items.all() if run else []
    if run and run.action == "NORMALIZE":
        for item in items:
            item.normalize_check = ""
            item.normalize_report_path = ""
            if item.out_path:
                out_path = Path(item.out_path)
                report_path = out_path.parent / "normalize_report.json"
                report = _read_json(report_path)
                if report and "status" in report:
                    item.normalize_check = "OK" if report.get("status") == "OK" else "FAIL"
                elif report_path.exists():
                    item.normalize_check = "WARN"
                item.normalize_report_path = str(report_path)

    session_mode = request.session.get("pipeline_mode") or "MULTILANGUAGE"
    session_books = request.session.get("pipeline_books") or []
    session_langs = request.session.get("pipeline_languages") or []
    session_translate_mode = normalize_mode(
        request.session.get("translate_mode"), default="automatic"
    )

    if selected_book_code:
        selected_books = [selected_book_code]
    else:
        selected_books = session_books

    if session_langs:
        selected_languages = session_langs
    elif default_langs:
        selected_languages = default_langs
    else:
        selected_languages = ["en"]

    anchor_run_state = _runner_anchor_run_state(
        selected_books=selected_books,
        selected_languages=selected_languages,
    )
    if anchor_run_state:
        session_policy = _resolve_policy_for_state(
            run_state=anchor_run_state,
            fallback_selected_mode=session_translate_mode,
        )
    else:
        session_policy = apply_skip_policy(
            selected_mode=session_translate_mode,
            split_mode=request.session.get("split_mode"),
            refine_mode=request.session.get("refine_mode"),
        )

    request.session["translate_mode"] = session_policy["selected_mode"]
    request.session["split_mode"] = session_policy["split_mode"]
    request.session["refine_mode"] = session_policy["refine_mode"]

    context = {
        "works": works,
        "languages": languages,
        "selected_book_code": selected_book_code,
        "selected_books": selected_books,
        "selected_languages": selected_languages,
        "pipeline_mode": session_mode,
        "default_langs": default_langs,
        "translate_mode": session_policy["selected_mode"],
        "split_mode": session_policy["split_mode"],
        "refine_mode": session_policy["refine_mode"],
        "skip_locked_automatic": session_policy["effective_mode"] == "automatic",
        "run": run,
        "items": items,
    }
    return render(request, "pipeline/runner_matrix.html", context)


@require_POST
def runner_matrix_run_view(request):
    book_codes = [b for b in request.POST.getlist("books") if b.strip()]
    languages = [l for l in request.POST.getlist("languages") if l.strip()]
    action = (request.POST.get("action") or "TRANSLATE").upper()
    posted_mode = (request.POST.get("mode") or "").upper()
    mode = posted_mode if posted_mode in {"MULTILANGUAGE", "SEQUENTIAL"} else (request.session.get("pipeline_mode") or "MULTILANGUAGE")

    if posted_mode in {"MULTILANGUAGE", "SEQUENTIAL"}:
        request.session["pipeline_mode"] = posted_mode
    if book_codes:
        request.session["pipeline_books"] = book_codes
    if languages:
        request.session["pipeline_languages"] = languages

    running = PipelineRun.objects.filter(action=action, status="RUNNING").first()
    if running:
        messages.warning(request, f"Já existe um run em execução (#{running.id}).")
        return redirect("pipeline_runner_matrix_detail", run_id=running.id)

    if not book_codes:
        messages.error(request, "Selecione ao menos 1 book.")
        return redirect("pipeline_runner_matrix")

    if action in {"TRANSLATE", "TRANSLATE_DEFAULT", "SPLIT_FOR_REFINE", "BUILD", "EXPORT_EPUB"} and not languages:
        messages.error(request, "Selecione ao menos 1 idioma.")
        return redirect("pipeline_runner_matrix")

    if action not in {
        "NORMALIZE",
        "CHUNK",
        "TRANSLATE",
        "TRANSLATE_DEFAULT",
        "SPLIT_FOR_REFINE",
        "BUILD",
        "EXPORT_EPUB",
    }:
        messages.error(request, "Ação inválida no MVP.")
        return redirect("pipeline_runner_matrix")

    if mode not in {"MULTILANGUAGE", "SEQUENTIAL"}:
        messages.error(request, "Modo inválido.")
        return redirect("pipeline_runner_matrix")

    if action in {"TRANSLATE", "TRANSLATE_DEFAULT"}:
        if mode == "MULTILANGUAGE" and len(book_codes) != 1:
            messages.error(request, "Multilanguage mode exige 1 book.")
            return redirect("pipeline_runner_matrix")
        if mode == "SEQUENTIAL" and len(languages) != 1:
            messages.error(request, "Sequential mode exige 1 idioma.")
            return redirect("pipeline_runner_matrix")

    skip_existing = request.POST.get("skip_existing") == "on"
    stop_on_error = request.POST.get("stop_on_error") == "on"
    dry_run = request.POST.get("dry_run") == "on"
    posted_translate_mode = (
        request.POST.get("translate_mode")
        if "translate_mode" in request.POST
        else None
    )
    posted_split_mode = request.POST.get("split_mode") if "split_mode" in request.POST else None
    posted_refine_mode = request.POST.get("refine_mode") if "refine_mode" in request.POST else None

    session_translate_mode = normalize_mode(
        request.session.get("translate_mode"),
        default="automatic",
    )
    selected_mode_input = (
        "default"
        if action == "TRANSLATE_DEFAULT"
        else posted_translate_mode
    )

    anchor_run_state = _runner_anchor_run_state(
        selected_books=book_codes,
        selected_languages=languages,
    )
    if anchor_run_state:
        skip_policy = _resolve_policy_for_state(
            run_state=anchor_run_state,
            selected_mode=selected_mode_input,
            split_mode=posted_split_mode,
            refine_mode=posted_refine_mode,
            fallback_selected_mode=session_translate_mode,
        )
    else:
        selected_mode = (
            normalize_mode(selected_mode_input, default="automatic")
            if selected_mode_input is not None
            else session_translate_mode
        )
        skip_policy = apply_skip_policy(
            selected_mode=selected_mode,
            split_mode=posted_split_mode,
            refine_mode=posted_refine_mode,
        )

    split_mode = skip_policy["split_mode"]
    refine_mode = skip_policy["refine_mode"]

    if skip_policy["skip_corrected"]:
        messages.warning(request, "Skip is only allowed for DEFAULT mode.")

    request.session["translate_mode"] = skip_policy["selected_mode"]
    request.session["split_mode"] = split_mode
    request.session["refine_mode"] = refine_mode

    run_languages = languages
    if action in {"NORMALIZE", "CHUNK"}:
        run_languages = ["en"]
    if action == "NORMALIZE":
        blocked_books: list[str] = []
        for book_code in book_codes:
            edition = _edition_for_book_lang(book_code, "en")
            status = (edition.status if edition else "") or "MISSING"
            if status == EditorialEdition.STATUS_INGESTED:
                continue
            blocked_books.append(f"{book_code} ({status})")
        if blocked_books:
            messages.error(
                request,
                "Gate bloqueado: NORMALIZE exige status exatamente INGESTED "
                f"(books: {', '.join(blocked_books)}).",
            )
            return redirect("pipeline_runner_matrix")
    if action == "CHUNK":
        blocked_books: list[str] = []
        for book_code in book_codes:
            edition = _edition_for_book_lang(book_code, "en")
            status = (edition.status if edition else "") or "MISSING"
            if status == EditorialEdition.STATUS_FIXED_TEXT:
                continue
            blocked_books.append(f"{book_code} ({status})")
        if blocked_books:
            messages.error(
                request,
                "Gate bloqueado: CHUNK exige status FIXED_TEXT "
                f"(books: {', '.join(blocked_books)}). Rode NORMALIZE + FIX_TEXT antes.",
            )
            return redirect("pipeline_runner_matrix")

    _persist_policy_for_matrix_selection(
        book_codes=book_codes,
        run_languages=run_languages,
        policy=skip_policy,
    )

    run = PipelineRun.objects.create(
        mode="MATRIX",
        action=action,
        options={
            "queue_mode": True,
            "skip_existing": skip_existing,
            "stop_on_error": stop_on_error,
            "dry_run": dry_run,
            "mode": mode,
            "translate_mode": skip_policy["selected_mode"],
            "selected_mode": skip_policy["selected_mode"],
            "effective_mode": skip_policy["effective_mode"],
            "split_mode": split_mode,
            "refine_mode": refine_mode,
            "skip_requested": skip_policy["skip_requested"],
            "skip_applied": skip_policy["skip_applied"],
            "skip_block_reason": skip_policy["skip_block_reason"],
            "skip_corrected": skip_policy["skip_corrected"],
            "skip_original_split_mode": skip_policy.get("skip_original_split_mode"),
            "skip_original_refine_mode": skip_policy.get("skip_original_refine_mode"),
        },
        status="PENDING",
    )

    items = []
    for book_code in book_codes:
        book_id = _parse_book_id(book_code)
        for lang in run_languages:
            out_path = ""
            if book_id is not None:
                if action == "NORMALIZE":
                    out_path = str(_runner_normalized_path(book_code, lang))
                elif action == "CHUNK":
                    lang_code = utils.normalize_lang(lang)
                    out_path = str((_project_root() / "data" / "chunks" / book_code / lang_code / "chunks_manifest.json"))
                elif action == "SPLIT_FOR_REFINE":
                    out_path = str(_runner_split_dir_path(book_id, lang))
                elif action == "BUILD":
                    out_path = str(_runner_book_build_path(book_code, lang))
                elif action == "EXPORT_EPUB":
                    out_path = str(_runner_epub_path(book_code, lang))
                else:
                    out_path = str(_runner_merge_translate_path(book_id, lang))
            items.append(
                PipelineRunItem(
                    run=run,
                    book_id=book_id,
                    book_code=book_code,
                    lang=utils.normalize_lang(lang),
                    out_path=out_path,
                    status="PENDING",
                )
            )
    PipelineRunItem.objects.bulk_create(items)

    _spawn_runner_process(run.id)

    return redirect("pipeline_runner_matrix_detail", run_id=run.id)


def runner_matrix_detail_view(request, run_id: int):
    run = get_object_or_404(PipelineRun, id=run_id)
    items = run.items.all()
    works = Work.objects.order_by("code")
    languages = [
        {"code": "en", "label": "EN"},
        {"code": "de", "label": "DE"},
        {"code": "fr", "label": "FR"},
        {"code": "it", "label": "IT"},
        {"code": "es", "label": "ES"},
        {"code": "ptbr", "label": "PT-BR"},
    ]
    session_mode = request.session.get("pipeline_mode") or "MULTILANGUAGE"
    session_books = request.session.get("pipeline_books") or []
    session_langs = request.session.get("pipeline_languages") or []
    session_translate_mode = normalize_mode(
        request.session.get("translate_mode"), default="automatic"
    )
    selected_books = session_books
    selected_languages = session_langs or ["en"]
    if run and run.items.exists():
        first_item = run.items.order_by("id").first()
        if first_item:
            selected_books = [first_item.book_code] if first_item.book_code else selected_books
            selected_languages = [utils.normalize_lang(first_item.lang)] if first_item.lang else selected_languages

    anchor_run_state = _runner_anchor_run_state(
        selected_books=selected_books,
        selected_languages=selected_languages,
    )
    if anchor_run_state:
        session_policy = _resolve_policy_for_state(
            run_state=anchor_run_state,
            fallback_selected_mode=session_translate_mode,
        )
    else:
        session_policy = apply_skip_policy(
            selected_mode=session_translate_mode,
            split_mode=request.session.get("split_mode"),
            refine_mode=request.session.get("refine_mode"),
        )
    request.session["translate_mode"] = session_policy["selected_mode"]
    request.session["split_mode"] = session_policy["split_mode"]
    request.session["refine_mode"] = session_policy["refine_mode"]

    context = {
        "works": works,
        "languages": languages,
        "selected_book_code": "",
        "selected_books": selected_books,
        "selected_languages": selected_languages,
        "pipeline_mode": session_mode,
        "translate_mode": session_policy["selected_mode"],
        "split_mode": session_policy["split_mode"],
        "refine_mode": session_policy["refine_mode"],
        "skip_locked_automatic": session_policy["effective_mode"] == "automatic",
        "run": run,
        "items": items,
    }
    return render(request, "pipeline/runner_matrix.html", context)


def book_edition_list(request):
    editions = (
        EditorialEdition.objects.select_related("work", "language", "seal")
        .order_by("work__code", "language__code")
    )
    return render(request, "pipeline/book_edition_list.html", {"editions": editions})


def book_edition_edit(request, book_code=None, language=None):
    if request.method == "GET":
        if not book_code or not language:
            messages.warning(request, "Crie novos livros via Projects (Source of Truth).")
            return redirect("projects_new")

        lang_code = _project_lang_db_code(language)
        work = Work.objects.filter(code=book_code).first()
        if not work:
            messages.error(request, "Projeto não encontrado. Crie via Projects.")
            return redirect("projects_new")

        edition = EditorialEdition.objects.filter(work=work, language__code=lang_code).first()
        if not edition:
            messages.error(
                request,
                "Edição não encontrada. Crie via Runner/Projects e volte para editar.",
            )
            return redirect("projects_hub", book_code=book_code)

        seals = Seal.objects.filter(is_active=True).order_by("name")
        context = {
            "seals": seals,
            "edition": edition,
            "work": work,
        }
        return render(request, "pipeline/book_edition_form.html", context)

    book_code = request.POST.get("book_code", "").strip()
    lang_code = request.POST.get("language", "").strip()
    title = request.POST.get("title", "").strip()
    author_name = request.POST.get("author", "").strip()
    year_raw = request.POST.get("year", "").strip()
    seal_slug = request.POST.get("seal", "").strip() or "MantaQuest"

    if not book_code or not lang_code:
        messages.error(request, "Book code e idioma são obrigatórios.")
        return redirect("projects_new")

    work_obj = Work.objects.filter(code=book_code).first()
    if not work_obj:
        messages.error(request, "Projeto não encontrado. Crie via Projects.")
        return redirect("projects_new")

    language_obj, _ = Language.objects.get_or_create(
        code=_project_lang_db_code(lang_code),
        defaults={
            "name": lang_code.upper(),
            "native_name": lang_code.upper(),
            "is_active": True,
        },
    )

    seal_obj, _ = Seal.objects.get_or_create(
        slug=seal_slug,
        defaults={"name": seal_slug, "is_active": True},
    )

    year = None
    if year_raw:
        try:
            year = int(year_raw)
        except ValueError:
            messages.warning(request, "Ano inválido; salvando sem ano.")

    edition = EditorialEdition.objects.filter(work=work_obj, language=language_obj).first()
    if not edition:
        messages.error(request, "Edição não encontrada. Crie via Runner/Projects.")
        return redirect("projects_hub", book_code=book_code)

    if title:
        edition.title = title
    if author_name:
        edition.author = author_name
    if year is not None:
        edition.edition_year = year
    edition.seal = seal_obj
    edition.save(update_fields=["title", "author", "edition_year", "seal", "updated_at"])

    messages.success(request, "Edição atualizada.")
    return redirect("edition_steps", edition_id=edition.id)


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


def _select_contract_path(language: str) -> Path:
    return resolve_translate_contract_path(utils.normalize_lang(language))


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
    lang_dir = target_lang.strip().upper()
    return Path("data/translated") / f"book_{book_id:04d}" / lang_dir


def _resolve_core_path(path_value: str) -> Path:
    candidate = Path(path_value)
    if candidate.is_absolute():
        return candidate
    return Path(settings.BASE_DIR).parent / candidate


def _maybe_sync_book_0002_images(book_code: str) -> None:
    if book_code != "book_0002":
        return
    project_root = Path(settings.BASE_DIR).parent
    script_path = project_root / "gaiden" / "bin" / "book_0002_prebuild_images.sh"
    if not script_path.exists():
        raise FileNotFoundError(f"Prebuild script not found: {script_path}")

    result = subprocess.run(
        ["bash", str(script_path)],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Prebuild images failed.\n"
            f"STDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}"
        )


def _copy_merge_to_build(edition, merged_path: Path, target_path: Path) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(merged_path, target_path)
    return target_path


def _project_root() -> Path:
    return Path(settings.BASE_DIR).parent


def _get_or_create_run_state(edition: EditorialEdition) -> PipelineRunState:
    defaults = {
        "asset_language": utils.normalize_lang(getattr(edition.language, "code", "en")),
        "selected_mode": "automatic",
        "effective_mode": "automatic",
        "split_mode": "do",
        "refine_mode": "do",
    }
    state, _ = PipelineRunState.objects.get_or_create(edition=edition, defaults=defaults)
    if not state.asset_language:
        state.asset_language = defaults["asset_language"]
    if not state.selected_mode:
        state.selected_mode = defaults["selected_mode"]
    if not state.effective_mode:
        state.effective_mode = defaults["effective_mode"]
    if not state.split_mode:
        state.split_mode = defaults["split_mode"]
    if not state.refine_mode:
        state.refine_mode = defaults["refine_mode"]
    state.save(
        update_fields=[
            "asset_language",
            "selected_mode",
            "effective_mode",
            "split_mode",
            "refine_mode",
            "updated_at",
        ]
    )
    return state


def _edition_for_book_lang(book_code: str, language: str) -> EditorialEdition | None:
    db_lang = _project_lang_db_code(language)
    return EditorialEdition.objects.filter(
        work__code=book_code,
        language__code=db_lang,
    ).first()


def _edition_materialized_raw_exists(edition: EditorialEdition | None) -> bool:
    if not edition:
        return False
    for raw_path in (
        (edition.raw_materialized_path or "").strip(),
        (edition.raw_source_path or "").strip(),
    ):
        if not raw_path:
            continue
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = _project_root() / candidate
        if candidate.exists():
            return True
    return False


def _edition_is_ingested_or_better(edition: EditorialEdition | None) -> bool:
    if not edition:
        return False
    status = (edition.status or "").strip().upper()
    accepted = {
        EditorialEdition.STATUS_INGESTED,
        EditorialEdition.STATUS_NORMALIZED,
        EditorialEdition.STATUS_FIXED_TEXT,
        EditorialEdition.STATUS_CHUNKED,
        EditorialEdition.STATUS_TRANSLATED,
        EditorialEdition.STATUS_REFINED,
        EditorialEdition.STATUS_POLISHED,
        EditorialEdition.STATUS_CANONICAL_READY,
    }
    if status in accepted:
        return True
    return _edition_materialized_raw_exists(edition)


def _resolve_policy_for_state(
    *,
    run_state: PipelineRunState,
    selected_mode: str | None = None,
    split_mode: str | None = None,
    refine_mode: str | None = None,
    fallback_selected_mode: str = "automatic",
) -> dict:
    policy = run_state_policy.resolve_policy_from_state(
        run_state,
        selected_mode=selected_mode,
        split_mode=split_mode,
        refine_mode=refine_mode,
        fallback_selected_mode=fallback_selected_mode,
    )
    run_state_policy.apply_policy_to_state(run_state, policy)
    return policy


def _persist_policy_for_matrix_selection(
    *,
    book_codes: list[str],
    run_languages: list[str],
    policy: dict,
) -> None:
    for book_code in book_codes:
        for language in run_languages:
            edition = _edition_for_book_lang(book_code, language)
            if not edition:
                continue
            run_state = _get_or_create_run_state(edition)
            run_state_policy.apply_policy_to_state(run_state, policy)
            run_state.save(
                update_fields=[
                    "selected_mode",
                    "effective_mode",
                    "split_mode",
                    "refine_mode",
                    "updated_at",
                ]
            )


def _runner_anchor_run_state(
    *,
    selected_books: list[str],
    selected_languages: list[str],
) -> PipelineRunState | None:
    if not selected_books:
        return None
    anchor_book = selected_books[0]
    anchor_lang = selected_languages[0] if selected_languages else "en"
    edition = _edition_for_book_lang(anchor_book, anchor_lang)
    if not edition:
        return None
    return _get_or_create_run_state(edition)


def _cover_language_for_edition(edition: EditorialEdition) -> str:
    pipeline_state = EditionPipeline.objects.filter(edition=edition).first()
    lang_code = utils.normalize_lang(edition.language.code)
    if (
        pipeline_state
        and pipeline_state.frontmatter_locked
        and pipeline_state.frontmatter_language
    ):
        lang_code = utils.normalize_lang(pipeline_state.frontmatter_language)
    return lang_code


def _asset_language_from_request(
    request,
    default_language: str,
    run_state: PipelineRunState | None = None,
) -> str:
    raw = (request.POST.get("asset_language") or "").strip()
    if raw:
        return utils.normalize_lang(raw)
    if run_state and run_state.asset_language:
        return utils.normalize_lang(run_state.asset_language)
    return utils.normalize_lang(default_language)


def _save_uploaded_cover_original(
    *,
    cover_file,
    book_code: str,
    language: str,
) -> Path:
    ext = Path(cover_file.name).suffix.lower() or ".jpg"
    if ext not in image_pipeline.ALLOWED_IMAGE_EXTS:
        raise ValueError("Formato de capa invalido. Use png/webp/gif/jpg.")
    cover_dir = image_pipeline.covers_dir(book_code, language)
    cover_dir.mkdir(parents=True, exist_ok=True)
    for existing in cover_dir.glob("cover_original.*"):
        existing.unlink(missing_ok=True)
    target = cover_dir / f"cover_original{ext}"
    with target.open("wb+") as dest:
        for chunk in cover_file.chunks():
            dest.write(chunk)
    return target


def _save_uploaded_images_to_raw(
    *,
    uploads: list,
    book_code: str,
    language: str,
) -> tuple[int, list[str]]:
    raw_dir, _ = image_pipeline.ensure_image_dirs(book_code, language)
    saved = 0
    labels: list[str] = []
    for upload in uploads:
        original_name = Path(upload.name).name
        stem = image_pipeline.numeric_stem_or_raise(original_name)
        ext = Path(original_name).suffix.lower()
        idx = int(stem)
        for existing in raw_dir.iterdir():
            if not existing.is_file():
                continue
            existing_idx = image_pipeline.numeric_index_from_filename(existing.name)
            if existing_idx == idx:
                existing.unlink(missing_ok=True)
        target = raw_dir / f"{stem}{ext}"
        with target.open("wb+") as dest:
            for chunk in upload.chunks():
                dest.write(chunk)
        labels.append(f"{stem}{ext}")
        saved += 1
    return saved, labels


def _resolve_build_output_path(book_code: str, language: str) -> Path:
    return (
        _project_root()
        / "data"
        / "builds"
        / book_code
        / language
        / f"{book_code}_{language}_book.md"
    )


def _relpath_or_abs(path: Path) -> str:
    try:
        return path.relative_to(_project_root()).as_posix()
    except Exception:
        return str(path)


def _source_stats_from_chunk_dir(chunk_dir: Path) -> dict[str, int]:
    files = sorted(chunk_dir.glob("ch_*_chunk_*.txt"))
    text_parts: list[str] = []
    for path in files:
        if not path.is_file():
            continue
        text_parts.append(path.read_text(encoding="utf-8", errors="ignore").rstrip("\n"))
    merged = ("\n".join(text_parts).rstrip("\n") + "\n") if text_parts else ""
    lines = merged.splitlines()
    paragraph_count = 0
    in_para = False
    for line in lines:
        if line.strip():
            if not in_para:
                paragraph_count += 1
                in_para = True
        else:
            in_para = False
    return {
        "files": len(files),
        "bytes": len(merged.encode("utf-8")),
        "chars": len(merged),
        "lines": len(lines),
        "paragraphs": paragraph_count,
    }


def _resolve_translate_clean_path(
    *,
    result: dict,
    out_dir: Path,
    book_code: str,
    target_lang: str,
) -> Path | None:
    merged_txt = str(result.get("merged_txt") or "").strip()
    if merged_txt:
        merged_path = Path(merged_txt)
        if merged_path.exists():
            return merged_path

    active = resolve_active_or_latest(out_dir, book_code, target_lang)
    if active and active.exists():
        return active

    candidates = [
        out_dir / "merge_refine_clean.txt",
        out_dir / f"{book_code}_merge_refine_clean.txt",
    ]
    candidates.extend(sorted(out_dir.glob("*_merge_refine_clean*.txt")))
    candidates.extend(sorted(out_dir.glob("*_merge_clean.txt")))

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _book_token_for_outputs(book_code: str) -> str:
    book_id = _parse_book_id(book_code)
    if book_id is not None:
        return f"book{book_id:04d}"
    digits = "".join(ch for ch in book_code if ch.isdigit())
    if digits:
        return f"book{int(digits):04d}"
    return book_code.replace("_", "")


def _translate_clean_output_name(book_code: str, mode: str) -> str:
    token = _book_token_for_outputs(book_code)
    normalized_mode = canonical.normalize_mode(mode)
    return f"{token}_{normalized_mode}_merge_refine_clean.txt"


def _translate_full_merge_name(book_code: str) -> str:
    token = _book_token_for_outputs(book_code)
    return f"{token}_full_merge.txt"


def _run_translate_and_promote(
    *,
    edition: EditorialEdition,
    target_language: str,
    selected_mode: str,
    promote_to_canonical: bool,
) -> dict:
    book_code = edition.work.code
    target_lang = utils.normalize_lang(target_language)
    source_lang = "en"
    mode = canonical.normalize_mode(selected_mode, default="full")

    project = _project_root()
    chunk_dir = project / "data" / "chunks" / book_code / source_lang
    out_dir = project / "data" / "translated" / book_code / target_lang
    out_dir.mkdir(parents=True, exist_ok=True)
    if not chunk_dir.exists():
        raise FileNotFoundError(f"Chunks nao encontrados: {chunk_dir}")

    require_openai_ready(dry_run=False, repo_root=project)

    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    run_dir = canonical.translate_run_dir(book_code, target_lang, run_id)
    canonical.write_translate_run_mode(book_code, target_lang, run_id, mode)

    input_stats = _source_stats_from_chunk_dir(chunk_dir)
    canonical.write_translate_run_json(book_code, target_lang, run_id, "input_stats.json", input_stats)

    if mode == "default":
        translate_result = run_agent_translate(
            book_id=book_code,
            chunk_dir=chunk_dir,
            out_dir=out_dir,
            suffix=target_lang,
            mode="default",
        )
    else:
        translate_result = run_translate_safe(
            book_id=book_code,
            chunk_dir=chunk_dir,
            out_dir=out_dir,
            suffix=target_lang,
            dry_run=False,
            selected_mode="automatic",
        )

    clean_path = _resolve_translate_clean_path(
        result=translate_result,
        out_dir=out_dir,
        book_code=book_code,
        target_lang=target_lang,
    )
    if not clean_path:
        raise FileNotFoundError(
            f"Artifact not found after translate. out_dir={out_dir}"
        )

    effective_mode = canonical.normalize_mode(
        str(translate_result.get("final_mode") or translate_result.get("effective_route") or mode),
        default=mode,
    )
    output_stats = canonical.text_stats(clean_path)
    canonical.write_translate_run_json(book_code, target_lang, run_id, "output_stats.json", output_stats)

    copied_clean = canonical.copy_translate_run_output(
        book_code,
        target_lang,
        run_id,
        clean_path,
        _translate_clean_output_name(book_code, effective_mode),
    )
    if effective_mode == "full":
        canonical.copy_translate_run_output(
            book_code,
            target_lang,
            run_id,
            clean_path,
            _translate_full_merge_name(book_code),
        )

    log_payload = {
        "selected_mode": mode,
        "effective_mode": effective_mode,
        "result": translate_result,
        "translate_safe_report": _read_json(out_dir / "translate_safe_run_report.json"),
        "agent_translate_report": _read_json(out_dir / "agent_translate_run_report.json"),
    }
    canonical.write_translate_run_log(
        book_code,
        target_lang,
        run_id,
        "translate.log",
        json.dumps(log_payload, ensure_ascii=False, indent=2),
    )

    promoted = None
    if promote_to_canonical:
        enforce_ratio = target_lang == "en"
        promoted = canonical.promote_clean_to_canonical(
            book_code,
            target_lang,
            effective_mode,
            clean_path,
            source_stats=input_stats if enforce_ratio else None,
            enforce_ratio=enforce_ratio,
            meta={
                "run_id": run_id,
                "selected_mode": mode,
                "effective_mode": effective_mode,
                "translate_out_dir": _relpath_or_abs(out_dir),
                "copied_output": _relpath_or_abs(copied_clean),
            },
        )

    validate_payload = {
        "clean_path": _relpath_or_abs(clean_path),
        "copied_output": _relpath_or_abs(copied_clean),
        "output_stats": output_stats,
        "promoted": bool(promoted),
        "canonical_active": _relpath_or_abs(promoted["active_path"]) if promoted else None,
        "canonical_meta": _relpath_or_abs(promoted["active_json_path"]) if promoted else None,
    }
    canonical.write_translate_run_log(
        book_code,
        target_lang,
        run_id,
        "validate.log",
        json.dumps(validate_payload, ensure_ascii=False, indent=2),
    )

    return {
        "book_code": book_code,
        "target_language": target_lang,
        "selected_mode": mode,
        "effective_mode": effective_mode,
        "run_id": run_id,
        "run_dir": run_dir,
        "out_dir": out_dir,
        "clean_path": clean_path,
        "copied_clean_path": copied_clean,
        "input_stats": input_stats,
        "output_stats": output_stats,
        "translate_result": translate_result,
        "promoted": promoted,
    }


def _run_fasttrack_from_canonical(
    *,
    edition: EditorialEdition,
    language: str,
    run_state: PipelineRunState,
) -> dict:
    lang = utils.normalize_lang(language)
    book_code = edition.work.code
    canonical_info = canonical.canonical_status(book_code, lang)
    if not canonical_info.get("fasttrack_ready"):
        reason = canonical_info.get("reason") or "canonical_missing"
        raise RuntimeError(f"FastTrack bloqueado: {reason}")

    active_txt = Path(canonical_info["active_path"])
    md_path = paths.miolo_md_path_for_language(book_code, lang)
    chapter_pattern = miolo_transform._pattern_for_language(lang)
    miolo_transform.txt_to_md(active_txt, md_path, chapter_pattern, lang)

    translated_miolo = paths.data_dir() / "translated" / book_code / lang / "miolo.md"
    translated_miolo.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(md_path, translated_miolo)

    canonical_active_md = canonical.canonical_active_md_path(book_code, lang)
    canonical_active_md.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(md_path, canonical_active_md)

    canonical_build_source = canonical.canonical_build_source_md_path(book_code, lang)
    canonical_build_source.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(md_path, canonical_build_source)

    insert_result = image_pipeline.apply_processed_images_to_miolo(
        book_code=book_code,
        language=lang,
        md_path=md_path,
    )
    sync_result = image_pipeline.sync_processed_images_into_build(book_code, lang)

    source_sha256 = canonical.sha256_file(active_txt)
    run_state.asset_language = lang
    run_state.inserted_images_count = int(insert_result.get("inserted_images_count", 0))
    run_state.md_path = str(md_path)
    run_state.md_source_sha256 = source_sha256
    run_state.md_generated_at = timezone.now()
    run_state.md_status = "generated"
    run_state.last_step = "fasttrack"
    run_state.status = "ok"
    run_state.build_outputs = {
        **(run_state.build_outputs or {}),
        "fasttrack": {
            "canonical_txt": str(active_txt),
            "canonical_md": str(canonical_active_md),
            "build_source_md": str(canonical_build_source),
            "translated_miolo": str(translated_miolo),
            "inserted_images_count": insert_result.get("inserted_images_count", 0),
            "insertion_warnings": insert_result.get("warnings", []),
            "build_images_dir": sync_result.get("build_images_dir", ""),
            "build_image_count": sync_result.get("image_count", 0),
        }
    }
    report_v2 = {
        "book": book_code,
        "lang": lang,
        "ts": timezone.now().isoformat(),
        "stage": "fasttrack",
        "canonical_txt": str(active_txt),
        "md_path": str(md_path),
        "inserted_images_count": insert_result.get("inserted_images_count", 0),
    }
    run_state.last_log = "REPORT_V2 " + json.dumps(report_v2, ensure_ascii=False, separators=(",", ":"))
    run_state.save(
        update_fields=[
            "asset_language",
            "inserted_images_count",
            "md_path",
            "md_source_sha256",
            "md_generated_at",
            "md_status",
            "last_step",
            "status",
            "build_outputs",
            "last_log",
            "updated_at",
        ]
    )

    return {
        "book_code": book_code,
        "language": lang,
        "canonical_txt": active_txt,
        "md_path": md_path,
        "canonical_md": canonical_active_md,
        "build_source_md": canonical_build_source,
        "translated_miolo": translated_miolo,
        "insert": insert_result,
        "sync": sync_result,
    }


def _run_save_pipeline(
    *,
    edition: EditorialEdition,
    asset_language: str,
    run_state: PipelineRunState,
    skip_policy: dict,
) -> dict:
    asset_language = utils.normalize_lang(asset_language)
    target_edition = _edition_for_language(edition, asset_language)
    book_code = edition.work.code

    cover_lang = _cover_language_for_edition(edition)
    cover_result = image_pipeline.convert_cover_to_jpg(book_code, cover_lang)
    if cover_result.get("cover_jpg_path"):
        edition.cover_filepath = cover_result["cover_jpg_path"]
        edition.save(update_fields=["cover_filepath", "updated_at"])
        run_state.cover_jpg_path = cover_result["cover_jpg_path"]

    convert_result = image_pipeline.convert_raw_images_to_processed(book_code, asset_language)
    run_state.images_converted_count = int(convert_result.get("converted_count", 0))
    run_state.last_image_conversion_ts = timezone.now()

    md_result = miolo_transform.ensure_md_uptodate(
        target_edition,
        cached_source_sha256=run_state.md_source_sha256 or None,
    )
    md_path = Path(str(md_result.get("path") or paths.miolo_md_path(target_edition)))
    md_action = str(md_result.get("md_action") or "generated")
    md_warnings = [str(x) for x in (md_result.get("warnings") or []) if str(x).strip()]
    source_txt = str(md_result.get("source_txt") or "")
    source_sha256 = str(md_result.get("source_sha256") or "")

    if md_action == "generated":
        run_state.md_generated_at = timezone.now()
        run_state.md_source_sha256 = source_sha256
        run_state.md_status = "generated"
        run_state.md_path = str(md_path)
    elif md_action == "skipped_up_to_date":
        run_state.md_source_sha256 = source_sha256
        run_state.md_status = "skipped_up_to_date"
        run_state.md_path = str(md_path)
    else:
        run_state.md_status = "error"

    insert_result = image_pipeline.apply_processed_images_to_miolo(
        book_code=book_code,
        language=asset_language,
        md_path=md_path,
    )
    sync_result = image_pipeline.sync_processed_images_into_build(book_code, asset_language)

    call_command(
        "build_book_text",
        book_code=book_code,
        language=target_edition.language.code,
    )
    build_output = _resolve_build_output_path(book_code, target_edition.language.code)

    run_state.asset_language = asset_language
    run_state_policy.apply_policy_to_state(run_state, skip_policy)
    run_state.inserted_images_count = int(insert_result.get("inserted_images_count", 0))
    warnings_all = list(md_warnings) + [str(x) for x in insert_result.get("warnings", [])]
    run_state.warnings = warnings_all
    run_state.build_outputs = {
        "book_md": str(build_output),
        "build_images_dir": sync_result.get("build_images_dir", ""),
        "build_image_count": sync_result.get("image_count", 0),
        "build_images": sync_result.get("images", []),
        "md_action": md_action,
        "active_merge_source": source_txt,
        "active_merge_sha256": source_sha256,
        "insertion_warnings": insert_result.get("warnings", []),
    }
    run_state.active_artifact_filename = build_output.name
    run_state.status = "ok"
    run_state.last_step = "build"
    run_state.last_build_ts = timezone.now()
    report_v2 = {
        "book": book_code,
        "lang": asset_language,
        "ts": timezone.now().isoformat(),
        "selected_mode": run_state.selected_mode,
        "effective_mode": run_state.effective_mode,
        "split_mode": run_state.split_mode,
        "refine_mode": run_state.refine_mode,
        "md_action": md_action,
        "warnings_summary": warnings_all,
    }
    report_line = "REPORT_V2 " + json.dumps(report_v2, ensure_ascii=False, separators=(",", ":"))
    logger.info(report_line)
    run_state.last_log = report_line
    run_state.save()

    return {
        "cover_result": cover_result,
        "conversion": convert_result,
        "md": md_result,
        "insert": insert_result,
        "sync": sync_result,
        "md_path": str(md_path),
        "md_action": md_action,
        "warnings": warnings_all,
        "build_output": str(build_output),
    }


def _resolve_return_flow_contract(language: str) -> Path:
    lang = utils.normalize_lang(language)
    contract = RETURN_FLOW_CONTRACTS.get(lang)
    if not contract:
        raise ValueError(f"Refine/Polish via agente não disponível para language={lang}")
    return _project_root() / contract


def _extract_book_code(value: str) -> str | None:
    m = re.search(r"(book_\d{4})", value)
    return m.group(1) if m else None


def _runner_lang_dir(lang: str) -> str:
    return utils.normalize_lang(lang)


def _runner_merge_translate_path(book_id: int, lang: str) -> Path:
    data_dir = _project_root() / "data"
    book_code = f"book_{book_id:04d}"
    lang_dir = _runner_lang_dir(lang)
    return (
        data_dir
        / "translated"
        / book_code
        / lang_dir
        / active_pointer_filename(book_code, lang)
    )


def _runner_normalized_path(book_code: str, lang: str) -> Path:
    data_dir = _project_root() / "data"
    lang_code = utils.normalize_lang(lang)
    lang_dir = _fs_lang_dir(lang_code)
    return data_dir / "normalized" / book_code / lang_dir / f"{book_code}_{lang_code}_v2.txt"


def _normalized_md_path(book_code: str, lang: str) -> Path:
    data_dir = _project_root() / "data"
    lang_code = utils.normalize_lang(lang)
    lang_dir = _fs_lang_dir(lang_code)
    return data_dir / "normalized" / book_code / lang_dir / "normalized.md"


def _fixed_md_path(book_code: str, lang: str) -> Path:
    data_dir = _project_root() / "data"
    lang_code = utils.normalize_lang(lang)
    lang_dir = _fs_lang_dir(lang_code)
    return data_dir / "normalized" / book_code / lang_dir / "normalized.fixed.md"


def _sha256_or_blank(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return canonical_index.sha256_file(path)
    except Exception:
        return ""


def _latest_fix_report_for_book(book_code: str) -> dict | None:
    return fix_text.latest_fix_report(book_code)


def _runner_split_dir_path(book_id: int, lang: str) -> Path:
    data_dir = _project_root() / "data"
    lang_dir = _runner_lang_dir(lang)
    return data_dir / "translated" / f"book_{book_id:04d}" / lang_dir / "split_chapters_for_refine"


def _runner_book_build_path(book_code: str, lang: str) -> Path:
    data_dir = _project_root() / "data"
    db_lang = _project_lang_db_code(lang)
    return data_dir / "builds" / book_code / db_lang / f"{book_code}_{db_lang}_book.md"


def _runner_epub_path(book_code: str, lang: str) -> Path:
    data_dir = _project_root() / "data"
    db_lang = _project_lang_db_code(lang)
    return data_dir / "builds" / book_code / db_lang / "BOOK.epub"


def _runner_python_path() -> Path:
    venv_python = _project_root() / ".venv" / "bin" / "python"
    if venv_python.exists():
        return venv_python
    return Path(sys.executable)


def _spawn_runner_process(run_id: int) -> None:
    cmd = [
        str(_runner_python_path()),
        "web/manage.py",
        "run_pipeline_matrix",
        str(run_id),
    ]
    subprocess.Popen(
        cmd,
        cwd=str(_project_root()),
        env=os.environ.copy(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _run_return_flow(contract_path: Path, *, book_code: str) -> tuple[Path, str]:
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    splits_dir = payload.get("splits_dir", "")
    out_dir = payload.get("out_dir", "")
    merge_name = payload.get("merge_name", "merge_refine.txt")

    for probe in (splits_dir, out_dir):
        found = _extract_book_code(str(probe))
        if found and found != book_code:
            raise ValueError(
                f"Contrato {contract_path} aponta para {found}, mas edição é {book_code}."
            )

    splits_path = Path(splits_dir)
    if not splits_path.is_absolute():
        splits_path = _project_root() / splits_path
    if not splits_path.exists():
        raise FileNotFoundError(f"Split dir não encontrado: {splits_path}")

    out_path = Path(out_dir)
    if not out_path.is_absolute():
        out_path = _project_root() / out_path
    merge_path = out_path / merge_name

    cmd = [sys.executable, "-m", "gaiden.return_flow_runner", str(contract_path)]
    result = subprocess.run(
        cmd,
        cwd=str(_project_root()),
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"return_flow_runner falhou.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    if not merge_path.exists():
        raise FileNotFoundError(f"Merge não encontrado: {merge_path}")

    return merge_path, result.stdout.strip()


def _detect_merged_path(out_dir: Path) -> Path | None:
    lang_key = out_dir.name
    book_code = out_dir.parent.name
    return resolve_active_or_latest(out_dir, book_code, lang_key)


def _detect_translate_report_path(out_dir: Path) -> Path:
    candidates = [
        out_dir / "translate_safe_run_report.json",
        out_dir / "agent_translate_run_report.json",
        out_dir / "translate_run_report.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def _count_chunks(book_code: str) -> int | None:
    book_id = _parse_book_id(book_code)
    if book_id is None:
        return None
    data_dir = Path(settings.BASE_DIR).parent / "data"
    chunks_dir = data_dir / "chunks" / f"book_{book_id:04d}" / "en"
    if not chunks_dir.is_dir():
        return None
    return len(list(chunks_dir.glob("ch_*_chunk_*.txt")))


def _translate_runtime_dir() -> Path:
    return _project_root() / "data" / "contracts_runtime"


def _translate_runtime_contract_path(mode: str) -> Path:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    name = "translate_multilang_run" if mode == "multilang" else "translate_queue_run"
    return _translate_runtime_dir() / f"{name}_{ts}.json"


def _translate_chunk_count(book_code: str, source_lang: str = "en") -> int:
    data_dir = _project_root() / "data"
    chunks_dir = data_dir / "chunks" / book_code / source_lang
    if not chunks_dir.is_dir():
        return 0
    return len(list(chunks_dir.glob("ch_*_chunk_*.txt")))


def _translate_available_books() -> list[dict]:
    data_dir = _project_root() / "data"
    chunks_root = data_dir / "chunks"
    books: list[dict] = []
    if chunks_root.is_dir():
        for entry in sorted(chunks_root.iterdir()):
            if not entry.is_dir():
                continue
            if not re.match(r"^book_\d{4}$", entry.name):
                continue
            if entry.name in TRANSLATE_LEGACY_BOOKS:
                continue
            count = _translate_chunk_count(entry.name, "en")
            books.append({"code": entry.name, "chunks": count})
    return books


def _translate_preview_lines(path: Path, limit: int = 20) -> list[str]:
    lines: list[str] = []
    if not path.exists():
        return lines
    try:
        with path.open("r", encoding="utf-8") as f:
            for _ in range(limit):
                line = f.readline()
                if not line:
                    break
                lines.append(line.rstrip("\n"))
    except OSError:
        return []
    return lines


def _translate_scan_patterns(path: Path) -> dict:
    if not path.exists():
        return {"commentary_ok": False, "markdown_ok": False, "commentary_hits": 0, "markdown_hits": 0}
    text = ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {"commentary_ok": False, "markdown_ok": False, "commentary_hits": 0, "markdown_hits": 0}

    commentary_re = re.compile(r"(DRY_RUN|NOTE:|Translator|As an AI|I can't|I cannot|Coment|Observa)", re.IGNORECASE)
    markdown_re = re.compile(r"(^#|```|\\*\\*|\\[.+\\]\\(.+\\))", re.MULTILINE)

    commentary_hits = len(commentary_re.findall(text))
    markdown_hits = len(markdown_re.findall(text))

    return {
        "commentary_ok": commentary_hits == 0,
        "markdown_ok": markdown_hits == 0,
        "commentary_hits": commentary_hits,
        "markdown_hits": markdown_hits,
    }


def _heading_hits_in_text(text: str) -> int:
    heading_re = re.compile(r"(CHAPTER|Chapter|SECTION|\\bI\\.|\\bII\\.|\\bIII\\.|\\bIV\\.|\\bV\\.)", re.MULTILINE)
    return len(heading_re.findall(text))


def _heading_hits_in_chunks(book_code: str, source_lang: str = "en") -> int:
    data_dir = _project_root() / "data"
    chunks_dir = data_dir / "chunks" / book_code / source_lang
    if not chunks_dir.is_dir():
        return 0
    hits = 0
    for path in chunks_dir.glob("ch_*_chunk_*.txt"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        hits += _heading_hits_in_text(text)
    return hits


def translate_control(request):
    books = _translate_available_books()
    book_map = {b["code"]: b for b in books}
    # Load secrets early so UI can show accurate status.
    require_openai_ready(dry_run=True, repo_root=_project_root())
    openai_key_set = bool(os.environ.get("OPENAI_API_KEY"))

    context = {
        "books": books,
        "targets": TRANSLATE_TARGETS,
        "openai_key_set": openai_key_set,
        "errors": [],
        "warnings": [],
        "results": None,
        "mode": None,
        "job_estimate": None,
        "selected_book": None,
        "selected_targets": ["en"],
        "selected_queue": [],
        "selected_target_lang": "en",
        "run_output": None,
        "contract_path": None,
        "run_flags": {
            "dry_run": True,
            "resume": True,
            "fail_fast": True,
            "translate_mode": "automatic",
        },
    }

    if request.method != "POST":
        return render(request, "pipeline/translate_control.html", context)

    mode = (request.POST.get("mode") or "").strip()
    context["mode"] = mode

    if mode not in ("multilang", "multibook"):
        context["errors"].append("Modo inválido.")
        return render(request, "pipeline/translate_control.html", context)

    confirm_run = (request.POST.get("confirm_run") or "").strip() == "1"
    if not confirm_run:
        context["errors"].append("Confirmação obrigatória antes de executar.")
        return render(request, "pipeline/translate_control.html", context)

    dry_run = _parse_bool(request.POST.get("dry_run", True), default=True)
    resume = _parse_bool(request.POST.get("resume", True), default=True)
    fail_fast = _parse_bool(request.POST.get("fail_fast", True), default=True)
    translate_mode = normalize_mode(request.POST.get("translate_mode"), default="automatic")
    context["run_flags"] = {
        "dry_run": dry_run,
        "resume": resume,
        "fail_fast": fail_fast,
        "translate_mode": translate_mode,
    }

    # Ensure env is loaded before gating.
    require_openai_ready(dry_run=dry_run, repo_root=_project_root())
    openai_key_set = bool(os.environ.get("OPENAI_API_KEY"))
    context["openai_key_set"] = openai_key_set

    if dry_run:
        context["warnings"].append("DRY RUN ativo: nenhuma chamada à OpenAI será feita.")
    if not dry_run and not openai_key_set:
        context["errors"].append("OPENAI_API_KEY AUSENTE — necessário para execução real")
        return render(request, "pipeline/translate_control.html", context)

    data_dir = _project_root() / "data"
    chunks_root = data_dir / "chunks"
    translated_root = data_dir / "translated"

    if mode == "multilang":
        book = (request.POST.get("book_code") or "").strip()
        context["selected_book"] = book
        targets = request.POST.getlist("target_languages")
        targets = [t for t in targets if t in TRANSLATE_TARGET_CODES]
        targets = [normalize_lang_code(t, default="en") for t in targets]
        # Deduplicate while preserving order.
        seen = set()
        targets = [t for t in targets if not (t in seen or seen.add(t))]
        context["selected_targets"] = targets

        if not book:
            context["errors"].append("Selecione um book.")
        if book in TRANSLATE_LEGACY_BOOKS:
            context["errors"].append(
                "Legacy books bloqueados via UI. Use CLI com --allow-legacy apenas para diagnóstico."
            )
        if book and book not in book_map:
            context["errors"].append(f"Book inválido ou sem chunks: {book}.")
        if not targets:
            context["errors"].append("Selecione pelo menos um target language.")

        chunk_count = _translate_chunk_count(book, "en") if book else 0
        if book and chunk_count == 0:
            context["errors"].append(f"Chunks ausentes para {book} (data/chunks/{book}/en).")

        context["job_estimate"] = {
            "chunks": chunk_count,
            "targets": len(targets),
            "jobs": chunk_count * len(targets),
        }

        if context["errors"]:
            return render(request, "pipeline/translate_control.html", context)

        contract = {
            "schema": "gaiden_translate_multilang_v1",
            "translation_spec": "docs/TRANSLATE_SPEC_v1.md",
            "mode": "one_book_to_many_languages",
            "translate_mode": translate_mode,
            "book": book,
            "source_lang": "en",
            "target_languages": targets,
            "engine": {"provider": "openai", "model": "gpt-5.2"},
            "paths": {
                "chunks_root": "data/chunks",
                "translated_root": "data/translated",
                "runs_root": "data/translated/_runs",
            },
            "run": {
                "execution": "sequential",
                "resume": resume,
                "dry_run": dry_run,
                "fail_fast": fail_fast,
                "translate_mode": translate_mode,
            },
        }

        _translate_runtime_dir().mkdir(parents=True, exist_ok=True)
        contract_path = _translate_runtime_contract_path("multilang")
        contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
        context["contract_path"] = str(contract_path)

        cmd = [
            str(_runner_python_path()),
            str(_project_root() / "scripts" / "ops" / "translate_multilang_v1.py"),
            "--contract",
            str(contract_path),
        ]
        result = subprocess.run(
            cmd,
            cwd=str(_project_root()),
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            check=False,
        )
        context["run_output"] = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            context["errors"].append("Execução falhou. Veja o log abaixo.")
            return render(request, "pipeline/translate_control.html", context)

        items = []
        source_heading_hits = _heading_hits_in_chunks(book, "en")
        for lang in targets:
            out_dir = translated_root / book / lang
            merged = _detect_merged_path(out_dir) or (out_dir / "__missing_canonical__.txt")
            report = _detect_translate_report_path(out_dir)
            stamp = Path(str(merged) + ".STAMP.json")
            scans = _translate_scan_patterns(merged) if merged.exists() else None
            if scans is not None:
                merged_text = merged.read_text(encoding="utf-8", errors="ignore")
                merged_heading_hits = _heading_hits_in_text(merged_text)
                heading_expected = source_heading_hits > 0
                scans["heading_expected"] = heading_expected
                scans["heading_hits"] = merged_heading_hits
                scans["heading_ok"] = (not heading_expected) or merged_heading_hits > 0
            items.append(
                {
                    "book": book,
                    "lang": lang,
                    "out_dir": out_dir,
                    "merged": merged,
                    "report": report,
                    "stamp": stamp,
                    "merged_exists": merged.exists(),
                    "report_exists": report.exists(),
                    "stamp_exists": stamp.exists(),
                    "preview": _translate_preview_lines(merged, 20),
                    "scan": scans,
                }
            )

        context["results"] = {
            "mode": mode,
            "items": items,
        }
        return render(request, "pipeline/translate_control.html", context)

    queue_books = request.POST.getlist("queue_books")
    queue_books = [b for b in queue_books if b in book_map]
    context["selected_queue"] = queue_books
    target_lang = (request.POST.get("target_lang") or "").strip()
    if target_lang not in TRANSLATE_TARGET_CODES:
        target_lang = "en"
    target_lang = normalize_lang_code(target_lang, default="en")
    context["selected_target_lang"] = target_lang

    if not queue_books:
        context["errors"].append("Selecione ao menos um book na fila.")
    if any(b in TRANSLATE_LEGACY_BOOKS for b in queue_books):
        context["errors"].append(
            "Legacy books bloqueados via UI. Use CLI com --allow-legacy apenas para diagnóstico."
        )

    per_book = []
    total_jobs = 0
    for book in queue_books:
        count = _translate_chunk_count(book, "en")
        per_book.append({"book": book, "chunks": count})
        total_jobs += count
        if count == 0:
            context["errors"].append(f"Chunks ausentes para {book} (data/chunks/{book}/en).")

    context["job_estimate"] = {
        "chunks": total_jobs,
        "targets": 1,
        "jobs": total_jobs,
    }

    if context["errors"]:
        return render(request, "pipeline/translate_control.html", context)

    contract = {
        "schema": "gaiden_translate_queue_v1",
        "translation_spec": "docs/TRANSLATE_SPEC_v1.md",
        "mode": "many_books_to_one_language",
        "translate_mode": translate_mode,
        "source_lang": "en",
        "target_lang": target_lang,
        "queue": [{"book": b} for b in queue_books],
        "engine": {"provider": "openai", "model": "gpt-5.2"},
        "paths": {
            "chunks_root": "data/chunks",
            "translated_root": "data/translated",
            "runs_root": "data/translated/_runs",
        },
        "run": {
            "execution": "sequential",
            "resume": resume,
            "dry_run": dry_run,
            "fail_fast": fail_fast,
            "translate_mode": translate_mode,
        },
    }

    _translate_runtime_dir().mkdir(parents=True, exist_ok=True)
    contract_path = _translate_runtime_contract_path("multibook")
    contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    context["contract_path"] = str(contract_path)

    cmd = [
        str(_runner_python_path()),
        str(_project_root() / "scripts" / "ops" / "translate_queue_v1.py"),
        "--contract",
        str(contract_path),
    ]
    result = subprocess.run(
        cmd,
        cwd=str(_project_root()),
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        check=False,
    )
    context["run_output"] = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        context["errors"].append("Execução falhou. Veja o log abaixo.")
        return render(request, "pipeline/translate_control.html", context)

    items = []
    for book in queue_books:
        out_dir = translated_root / book / target_lang
        merged = _detect_merged_path(out_dir) or (out_dir / "__missing_canonical__.txt")
        report = _detect_translate_report_path(out_dir)
        stamp = Path(str(merged) + ".STAMP.json")
        scans = _translate_scan_patterns(merged) if merged.exists() else None
        if scans is not None:
            merged_text = merged.read_text(encoding="utf-8", errors="ignore")
            merged_heading_hits = _heading_hits_in_text(merged_text)
            source_heading_hits = _heading_hits_in_chunks(book, "en")
            heading_expected = source_heading_hits > 0
            scans["heading_expected"] = heading_expected
            scans["heading_hits"] = merged_heading_hits
            scans["heading_ok"] = (not heading_expected) or merged_heading_hits > 0
        items.append(
            {
                "book": book,
                "lang": target_lang,
                "out_dir": out_dir,
                "merged": merged,
                "report": report,
                "stamp": stamp,
                "merged_exists": merged.exists(),
                "report_exists": report.exists(),
                "stamp_exists": stamp.exists(),
                "preview": _translate_preview_lines(merged, 20),
                "scan": scans,
            }
        )

    context["results"] = {
        "mode": mode,
        "items": items,
        "queue_summary": per_book,
    }
    return render(request, "pipeline/translate_control.html", context)


def edition_steps(request, edition_id: int):
    edition = get_object_or_404(EditorialEdition, id=edition_id)
    book_code, language = _edition_codes(edition)
    run_state = _get_or_create_run_state(edition)

    if request.method == "POST":
        action = request.POST.get("action")
        cover_action = (request.POST.get("cover_action") or "").strip().lower()
        if cover_action == "upload":
            action = "upload_cover"
        elif cover_action == "convert":
            action = "convert_cover_jpg"

        if action == "materialize_raw":
            try:
                result = canonical_index.materialize_raw(edition)
            except Exception as exc:
                messages.error(request, f"Materialize RAW falhou: {exc}")
                return redirect("edition_steps", edition_id=edition.id)
            if result.get("skipped"):
                messages.info(
                    request,
                    "RAW ja estava materializado com o mesmo hash. "
                    f"path={result.get('raw_materialized_path')} sha={result.get('raw_sha256')}",
                )
            else:
                messages.success(
                    request,
                    "RAW materializado com sucesso. "
                    f"path={result.get('raw_materialized_path')} sha={result.get('raw_sha256')} "
                    f"run={result.get('canonical_run_dir')}",
                )
            return redirect("edition_steps", edition_id=edition.id)

        if action == "freeze_canonical":
            try:
                result = canonical_index.freeze_canonical(edition)
            except Exception as exc:
                messages.error(request, f"Freeze Canonical falhou: {exc}")
                return redirect("edition_steps", edition_id=edition.id)
            if result.get("skipped"):
                messages.info(
                    request,
                    "Freeze Canonical sem alteracoes (truth/assets inalterados). "
                    f"run={result.get('canonical_run_dir')}",
                )
            else:
                messages.success(
                    request,
                    "Canonical freeze concluido. "
                    f"truth={result.get('truth_path')} sha={result.get('truth_sha256')} "
                    f"run={result.get('canonical_run_dir')}",
                )
            return redirect("edition_steps", edition_id=edition.id)

        if action == "normalize_text":
            if (edition.status or "").strip().upper() != EditorialEdition.STATUS_INGESTED:
                messages.error(request, "Gate: NORMALIZE exige status INGESTED.")
                return redirect("edition_steps", edition_id=edition.id)
            lang_code = utils.normalize_lang(language)
            normalized_v2_path = _runner_normalized_path(book_code, lang_code)
            normalized_md_path = _normalized_md_path(book_code, lang_code)
            cmd = [str(_runner_python_path()), "-m", "gaiden.normalize", book_code, lang_code]
            result = subprocess.run(
                cmd,
                cwd=str(_project_root()),
                capture_output=True,
                text=True,
                env=os.environ.copy(),
                check=False,
            )
            if result.returncode != 0:
                messages.error(
                    request,
                    "Normalize falhou. "
                    f"stderr={((result.stderr or '').strip() or (result.stdout or '').strip())[:300]}",
                )
                return redirect("edition_steps", edition_id=edition.id)
            if not normalized_v2_path.exists():
                messages.error(request, f"Normalize não gerou output esperado: {normalized_v2_path}")
                return redirect("edition_steps", edition_id=edition.id)
            normalized_md_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_md = normalized_md_path.with_suffix(".md.tmp")
            tmp_md.write_text(normalized_v2_path.read_text(encoding="utf-8"), encoding="utf-8")
            tmp_md.replace(normalized_md_path)
            texts, _ = EditionText.objects.get_or_create(edition=edition)
            texts.normalized_path = normalized_md_path.relative_to(_project_root()).as_posix()
            texts.normalized_text = ""
            texts.save(update_fields=["normalized_path", "normalized_text", "updated_at"])
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
            pipeline_state.normalized_at = timezone.now()
            pipeline_state.current_stage = PipelineStage.NORMALIZED
            pipeline_state.save(update_fields=["normalized_at", "current_stage"])
            edition.status = EditorialEdition.STATUS_NORMALIZED
            edition.save(update_fields=["status", "updated_at"])
            messages.success(
                request,
                "Normalize concluído. "
                f"path={normalized_md_path.relative_to(_project_root()).as_posix()}",
            )
            return redirect("edition_steps", edition_id=edition.id)

        if action == "fix_text":
            if (edition.status or "").strip().upper() != EditorialEdition.STATUS_NORMALIZED:
                messages.error(request, "Gate: FIX_TEXT exige status NORMALIZED.")
                return redirect("edition_steps", edition_id=edition.id)
            try:
                report = fix_text.fix_text(edition)
            except Exception as exc:
                messages.error(request, f"FIX_TEXT falhou: {exc}")
                return redirect("edition_steps", edition_id=edition.id)
            if report.get("status") == "PASS":
                edition.status = EditorialEdition.STATUS_FIXED_TEXT
                edition.save(update_fields=["status", "updated_at"])
                messages.success(
                    request,
                    "FIX_TEXT concluído. "
                    f"output={report.get('output_path')} run={report.get('run_dir')}",
                )
            else:
                edition.status = EditorialEdition.STATUS_NORMALIZED
                edition.save(update_fields=["status", "updated_at"])
                heading_diff = report.get("heading_diff") or {}
                messages.error(
                    request,
                    "FIX_TEXT falhou (heading inventory). "
                    f"reason={heading_diff.get('reason', 'unknown')} run={report.get('run_dir')}",
                )
            return redirect("edition_steps", edition_id=edition.id)

        if action == "chunk_from_fixed":
            if (edition.status or "").strip().upper() != EditorialEdition.STATUS_FIXED_TEXT:
                messages.error(request, "Gate: CHUNKS exige status FIXED_TEXT.")
                return redirect("edition_steps", edition_id=edition.id)
            chunk_lang = utils.normalize_lang(language)
            if chunk_lang != "en":
                messages.error(request, "CHUNKS desta etapa suporta apenas EN.")
                return redirect("edition_steps", edition_id=edition.id)
            fixed_path = _fixed_md_path(book_code, chunk_lang)
            if not fixed_path.exists():
                messages.error(
                    request,
                    "normalized.fixed.md ausente. Rode FIX_TEXT antes de gerar CHUNKS.",
                )
                return redirect("edition_steps", edition_id=edition.id)
            try:
                chunk_result = chapter_chunks.run_chapter_chunks(edition, normalized_override=fixed_path)
            except Exception as exc:
                messages.error(request, f"CHUNKS falhou: {exc}")
                return redirect("edition_steps", edition_id=edition.id)
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
            pipeline_state.chunked_at = timezone.now()
            pipeline_state.current_stage = PipelineStage.CHUNKED
            pipeline_state.save(update_fields=["chunked_at", "current_stage"])
            edition.status = EditorialEdition.STATUS_CHUNKED
            edition.save(update_fields=["status", "updated_at"])
            messages.success(
                request,
                "CHUNKS concluído a partir de normalized.fixed.md. "
                f"manifest={chunk_result.get('manifest')}",
            )
            return redirect("edition_steps", edition_id=edition.id)

        if action == "upload_cover":
            cover_file = request.FILES.get("cover_file")
            if not cover_file:
                messages.error(request, "Selecione uma imagem de capa.")
                return redirect("edition_steps", edition_id=edition.id)
            try:
                cover_lang = _cover_language_for_edition(edition)
                target = _save_uploaded_cover_original(
                    cover_file=cover_file,
                    book_code=book_code,
                    language=cover_lang,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("edition_steps", edition_id=edition.id)
            rel = target.relative_to(_project_root()).as_posix()
            run_state.last_step = "upload_cover"
            run_state.status = "pending"
            run_state.save(update_fields=["last_step", "status", "updated_at"])
            messages.success(request, f"Cover original salvo: {rel}")
            return redirect("edition_steps", edition_id=edition.id)

        if action == "convert_cover_jpg":
            cover_lang = _cover_language_for_edition(edition)
            cover_file = request.FILES.get("cover_file")
            if cover_file:
                try:
                    _save_uploaded_cover_original(
                        cover_file=cover_file,
                        book_code=book_code,
                        language=cover_lang,
                    )
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return redirect("edition_steps", edition_id=edition.id)
            result = image_pipeline.convert_cover_to_jpg(book_code, cover_lang)
            cover_jpg_path = (result.get("cover_jpg_path") or "").strip()
            if not cover_jpg_path:
                messages.error(request, "Nenhuma capa encontrada para converter (cover_original.* / cover.*).")
                return redirect("edition_steps", edition_id=edition.id)
            edition.cover_filepath = cover_jpg_path
            edition.save(update_fields=["cover_filepath", "updated_at"])
            run_state.cover_jpg_path = cover_jpg_path
            run_state.last_step = "convert_cover"
            run_state.status = "ok"
            run_state.save(update_fields=["cover_jpg_path", "last_step", "status", "updated_at"])
            if result.get("converted"):
                messages.success(request, f"Capa convertida para JPG: {cover_jpg_path}")
            else:
                messages.success(request, f"Capa JPG já atualizada: {cover_jpg_path}")
            return redirect("edition_steps", edition_id=edition.id)

        if action == "upload_images_zip":
            images_zip = request.FILES.get("images_zip")
            if not images_zip:
                messages.error(request, "Selecione um ZIP de imagens.")
                return redirect("edition_steps", edition_id=edition.id)
            lang_code = _asset_language_from_request(request, language, run_state)
            raw_dir, _ = image_pipeline.ensure_image_dirs(book_code, lang_code)
            extracted: list[tuple[zipfile.ZipInfo, str, str]] = []
            invalid_name: list[str] = []
            invalid_format: list[str] = []
            seen: set[str] = set()
            try:
                with zipfile.ZipFile(images_zip) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        name = info.filename
                        if "__MACOSX" in name or name.endswith(".DS_Store"):
                            continue
                        filename = Path(name).name
                        if not filename or filename.startswith("."):
                            continue
                        ext = Path(filename).suffix.lower()
                        if ext not in image_pipeline.ALLOWED_IMAGE_EXTS:
                            invalid_format.append(filename)
                            continue
                        if not image_pipeline.validate_numeric_image_filename(filename):
                            invalid_name.append(filename)
                            continue
                        stem = image_pipeline.numeric_stem_or_raise(filename)
                        if stem in seen:
                            invalid_name.append(filename)
                            continue
                        seen.add(stem)
                        extracted.append((info, stem, ext))
            except zipfile.BadZipFile:
                messages.error(request, "ZIP invalido.")
                return redirect("edition_steps", edition_id=edition.id)

            if invalid_name:
                preview = ", ".join(sorted(set(invalid_name))[:5])
                messages.error(
                    request,
                    "Image name must be numeric: 00, 01, 02... "
                    f"Arquivos invalidos: {preview}",
                )
                return redirect("edition_steps", edition_id=edition.id)
            if invalid_format:
                preview = ", ".join(invalid_format[:3])
                messages.error(request, f"Formato de imagem invalido no ZIP: {preview}")
                return redirect("edition_steps", edition_id=edition.id)

            if not extracted:
                messages.error(request, "ZIP nao contem imagens validas.")
                return redirect("edition_steps", edition_id=edition.id)

            images_zip.seek(0)
            with zipfile.ZipFile(images_zip) as zf:
                for info, stem, ext in extracted:
                    idx = int(stem)
                    for existing in raw_dir.iterdir():
                        if not existing.is_file():
                            continue
                        existing_idx = image_pipeline.numeric_index_from_filename(existing.name)
                        if existing_idx == idx:
                            existing.unlink(missing_ok=True)
                    dest_path = raw_dir / f"{stem}{ext}"
                    with zf.open(info) as src, dest_path.open("wb") as dst:
                        shutil.copyfileobj(src, dst)

            run_state.asset_language = lang_code
            run_state.last_step = "upload_images"
            run_state.status = "pending"
            run_state.save(update_fields=["asset_language", "last_step", "status", "updated_at"])
            messages.success(request, f"{len(extracted)} imagem(ns) salvas em {raw_dir}.")
            return redirect("edition_steps", edition_id=edition.id)

        if action == "upload_images_individual":
            files = request.FILES.getlist("images_files")
            if not files:
                messages.error(request, "Selecione uma ou mais imagens.")
                return redirect("edition_steps", edition_id=edition.id)
            lang_code = _asset_language_from_request(request, language, run_state)
            try:
                saved, _ = _save_uploaded_images_to_raw(
                    uploads=files,
                    book_code=book_code,
                    language=lang_code,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect("edition_steps", edition_id=edition.id)
            run_state.asset_language = lang_code
            run_state.last_step = "upload_images"
            run_state.status = "pending"
            run_state.save(update_fields=["asset_language", "last_step", "status", "updated_at"])
            messages.success(request, f"{saved} imagem(ns) salvas no raw.")
            return redirect("edition_steps", edition_id=edition.id)

        if action == "convert_images_jpg":
            lang_code = _asset_language_from_request(request, language, run_state)
            try:
                result = image_pipeline.convert_raw_images_to_processed(book_code, lang_code)
            except Exception as exc:
                messages.error(request, f"Falha na conversao de imagens: {exc}")
                return redirect("edition_steps", edition_id=edition.id)

            if int(result.get("raw_count", 0)) == 0:
                run_state.asset_language = lang_code
                run_state.last_step = "convert_images"
                run_state.status = "pending"
                run_state.save(update_fields=["asset_language", "last_step", "status", "updated_at"])
                messages.warning(
                    request,
                    "Nenhuma imagem encontrada no RAW para converter. "
                    "Nada foi apagado no processed.",
                )
                return redirect("edition_steps", edition_id=edition.id)

            run_state.asset_language = lang_code
            run_state.images_converted_count = int(result.get("converted_count", 0))
            run_state.last_image_conversion_ts = timezone.now()
            run_state.last_step = "convert_images"
            run_state.status = "ok"
            run_state.save(
                update_fields=[
                    "asset_language",
                    "images_converted_count",
                    "last_image_conversion_ts",
                    "last_step",
                    "status",
                    "updated_at",
                ]
            )
            messages.success(
                request,
                "Conversao concluida. "
                f"total={result.get('raw_count', 0)} "
                f"convertidas={result.get('converted_count', 0)} "
                f"ignoradas={result.get('skipped_count', 0)}",
            )
            return redirect("edition_steps", edition_id=edition.id)

        if action in {"translate_full", "translate_default"}:
            lang_code = _asset_language_from_request(request, language, run_state)
            selected_mode = "default" if action == "translate_default" else "full"
            promote_on_success = _parse_bool(
                request.POST.get("promote_on_success", "on"),
                default=True,
            )
            fasttrack_after = _parse_bool(
                request.POST.get("fasttrack_after", ""),
                default=False,
            )
            try:
                translate_result = _run_translate_and_promote(
                    edition=edition,
                    target_language=lang_code,
                    selected_mode=selected_mode,
                    promote_to_canonical=promote_on_success,
                )
            except Exception as exc:
                messages.error(request, f"Translate falhou: {exc}")
                return redirect("edition_steps", edition_id=edition.id)

            effective_mode = str(translate_result.get("effective_mode") or selected_mode)
            run_dir = Path(translate_result["run_dir"])
            clean_path = Path(translate_result["clean_path"])
            promoted = translate_result.get("promoted")

            run_state.asset_language = lang_code
            run_state.selected_mode = "default" if selected_mode == "default" else "automatic"
            run_state.effective_mode = "default" if effective_mode == "default" else "automatic"
            run_state.active_artifact_filename = clean_path.name
            run_state.last_step = "translate"
            run_state.status = "ok"
            run_state.build_outputs = {
                **(run_state.build_outputs or {}),
                "translate_last": {
                    "run_id": translate_result.get("run_id"),
                    "run_dir": str(run_dir),
                    "clean_path": str(clean_path),
                    "promoted": bool(promoted),
                },
            }
            run_state.save(
                update_fields=[
                    "asset_language",
                    "selected_mode",
                    "effective_mode",
                    "active_artifact_filename",
                    "last_step",
                    "status",
                    "build_outputs",
                    "updated_at",
                ]
            )

            if promoted:
                active_path = Path(promoted["active_path"])
                messages.success(
                    request,
                    "Translate concluido e promovido para canonical. "
                    f"modo={effective_mode} "
                    f"clean={_relpath_or_abs(clean_path)} "
                    f"active={_relpath_or_abs(active_path)} "
                    f"run={_relpath_or_abs(run_dir)}",
                )
            else:
                messages.warning(
                    request,
                    "Translate concluido sem promoção canônica. "
                    f"clean={_relpath_or_abs(clean_path)} "
                    f"run={_relpath_or_abs(run_dir)}",
                )

            if fasttrack_after:
                if not promoted:
                    messages.error(
                        request,
                        "FastTrack after canonical marcado, mas canonical nao foi promovido.",
                    )
                    return redirect("edition_steps", edition_id=edition.id)
                try:
                    fasttrack = _run_fasttrack_from_canonical(
                        edition=edition,
                        language=lang_code,
                        run_state=run_state,
                    )
                except Exception as exc:
                    messages.error(request, f"FastTrack falhou apos translate: {exc}")
                    return redirect("edition_steps", edition_id=edition.id)
                messages.success(
                    request,
                    "FastTrack concluido. "
                    f"md={_relpath_or_abs(Path(fasttrack['md_path']))} "
                    f"build_source={_relpath_or_abs(Path(fasttrack['build_source_md']))}",
                )
            return redirect("edition_steps", edition_id=edition.id)

        if action == "promote_latest_clean":
            lang_code = _asset_language_from_request(request, language, run_state)
            preferred_mode = (request.POST.get("preferred_mode") or "").strip().lower() or None
            try:
                promoted = canonical.repromote_latest(
                    book_code,
                    lang_code,
                    preferred_mode=preferred_mode,
                    meta={"trigger": "edition_steps.promote_latest_clean"},
                )
            except Exception as exc:
                messages.error(request, f"Promote latest clean falhou: {exc}")
                return redirect("edition_steps", edition_id=edition.id)

            run_state.asset_language = lang_code
            run_state.last_step = "promote_canonical"
            run_state.status = "ok"
            run_state.save(update_fields=["asset_language", "last_step", "status", "updated_at"])
            messages.success(
                request,
                "Canonical pointer atualizado. "
                f"active={_relpath_or_abs(Path(promoted['active_path']))}",
            )
            return redirect("edition_steps", edition_id=edition.id)

        if action == "fasttrack_text_to_md":
            lang_code = _asset_language_from_request(request, language, run_state)
            try:
                result = _run_fasttrack_from_canonical(
                    edition=edition,
                    language=lang_code,
                    run_state=run_state,
                )
            except Exception as exc:
                messages.error(request, f"FastTrack bloqueado/falhou: {exc}")
                return redirect("edition_steps", edition_id=edition.id)

            messages.success(
                request,
                "FastTrack concluido. "
                f"md={_relpath_or_abs(Path(result['md_path']))} "
                f"build_source={_relpath_or_abs(Path(result['build_source_md']))} "
                f"inseridas={result['insert'].get('inserted_images_count', 0)}",
            )
            return redirect("edition_steps", edition_id=edition.id)

        if action == "save_pipeline":
            lang_code = _asset_language_from_request(request, language, run_state)
            selected_mode_input = (
                request.POST.get("translate_mode")
                if "translate_mode" in request.POST
                else None
            )
            split_mode_input = (
                request.POST.get("split_mode")
                if "split_mode" in request.POST
                else None
            )
            refine_mode_input = (
                request.POST.get("refine_mode")
                if "refine_mode" in request.POST
                else None
            )
            save_policy = _resolve_policy_for_state(
                run_state=run_state,
                selected_mode=selected_mode_input,
                split_mode=split_mode_input,
                refine_mode=refine_mode_input,
                fallback_selected_mode=run_state.selected_mode or "automatic",
            )
            run_state.save(
                update_fields=[
                    "selected_mode",
                    "effective_mode",
                    "split_mode",
                    "refine_mode",
                    "updated_at",
                ]
            )
            try:
                result = _run_save_pipeline(
                    edition=edition,
                    asset_language=lang_code,
                    run_state=run_state,
                    skip_policy=save_policy,
                )
            except Exception as exc:
                messages.error(request, f"Falha no SAVE: {exc}")
                return redirect("edition_steps", edition_id=edition.id)

            if save_policy.get("skip_corrected"):
                messages.warning(request, "Skip is only allowed for DEFAULT mode.")
            if result.get("md_action") == "skipped_up_to_date":
                messages.warning(request, "Arquivo já convertido (MD up-to-date).")
            warn_count = len(result["insert"].get("warnings", []))
            if warn_count:
                messages.warning(request, f"SAVE concluido com {warn_count} warning(s) de insercao.")
            messages.success(
                request,
                "SAVE concluido. "
                f"imagens_convertidas={result['conversion'].get('converted_count', 0)} "
                f"md_action={result.get('md_action')} "
                f"inseridas={result['insert'].get('inserted_images_count', 0)} "
                f"build={result['build_output']}",
            )
            return redirect("edition_steps", edition_id=edition.id)

        messages.error(request, "Acao nao permitida nesta tela.")
        return redirect("edition_steps", edition_id=edition.id)

    data_dir = _project_root() / "data"
    book_id = _parse_book_id(book_code)
    languages = [
        {"code": "en", "label": "EN"},
        {"code": "de", "label": "DE"},
        {"code": "fr", "label": "FR"},
        {"code": "it", "label": "IT"},
        {"code": "es", "label": "ES"},
        {"code": "ptbr", "label": "PT-BR"},
    ]

    status_rows = []
    for lang in languages:
        code = utils.normalize_lang(lang["code"])
        lang_dir = _runner_lang_dir(code)
        translated_ok = False
        split_ok = False
        refine_ok = False
        polish_ok = False
        epub_ok = False
        if book_id is not None:
            out_dir = data_dir / "translated" / f"book_{book_id:04d}" / lang_dir
            translated_path = _detect_merged_path(out_dir)
            split_dir = out_dir / "split_chapters_for_refine"
            build_dir = data_dir / "builds" / book_code / code
            translated_ok = bool(translated_path and translated_path.exists())
            split_ok = split_dir.exists()
            refine_ok = (build_dir / "merge_refine.txt").exists()
            polish_ok = (build_dir / "merge_polish.txt").exists()
            epub_ok = bool(list(build_dir.glob("*.epub"))) if build_dir.exists() else False

        status_rows.append(
            {
                "code": code,
                "label": lang["label"],
                "translated_ok": translated_ok,
                "split_ok": split_ok,
                "refine_ok": refine_ok,
                "polish_ok": polish_ok,
                "epub_ok": epub_ok,
            }
        )

    asset_lang_default = run_state.asset_language or utils.normalize_lang(language)
    asset_lang_default = utils.normalize_lang(asset_lang_default)
    raw_dir = image_pipeline.images_raw_dir(book_code, asset_lang_default)
    processed_dir = image_pipeline.images_processed_dir(book_code, asset_lang_default)
    build_images_dir = image_pipeline.build_images_dir(book_code, asset_lang_default)
    cover_lang = _cover_language_for_edition(edition)
    cover_source = image_pipeline.find_cover_source(book_code, cover_lang)
    cover_source_rel = ""
    if cover_source:
        cover_source_rel = cover_source.relative_to(_project_root()).as_posix()
    processed_numbers = image_pipeline.list_processed_numbers(book_code, asset_lang_default)
    raw_count = 0
    if raw_dir.exists():
        raw_count = sum(1 for p in raw_dir.iterdir() if p.is_file())
    run_policy = run_state_policy.resolve_policy_from_state(
        run_state,
        fallback_selected_mode="automatic",
    )
    edition_status = (edition.status or "").strip().upper()
    normalize_lang_code = utils.normalize_lang(language)
    normalized_v2_path = _runner_normalized_path(book_code, normalize_lang_code)
    normalized_md_path = _normalized_md_path(book_code, normalize_lang_code)
    fixed_md_path = _fixed_md_path(book_code, normalize_lang_code)
    normalized_display_path = normalized_md_path if normalized_md_path.exists() else normalized_v2_path
    latest_fix_report = _latest_fix_report_for_book(book_code)
    fix_report_status = (latest_fix_report or {}).get("status", "")
    fix_run_dir = (latest_fix_report or {}).get("run_dir", "")

    canonical_index.sync_edition_identity(edition)
    freeze_source = canonical_index.resolve_truth_source_path(edition)
    canonical_info = canonical.canonical_status(book_code, asset_lang_default)
    canonical_active_rel = _relpath_or_abs(Path(canonical_info["active_path"]))
    canonical_json_rel = _relpath_or_abs(Path(canonical_info["active_json_path"]))
    latest_translate_run = canonical.latest_translate_run_dir(book_code, asset_lang_default)
    latest_translate_run_rel = _relpath_or_abs(latest_translate_run) if latest_translate_run else ""

    context = {
        "edition": edition,
        "book_code": book_code,
        "language": language,
        "languages": languages,
        "status_rows": status_rows,
        "cover_filepath": edition.cover_filepath,
        "cover_original_path": cover_source_rel,
        "asset_language_selected": asset_lang_default,
        "images_raw_dir": raw_dir.relative_to(_project_root()).as_posix(),
        "raw_images_count": raw_count,
        "images_processed_dir": processed_dir.relative_to(_project_root()).as_posix(),
        "build_images_dir": build_images_dir.relative_to(_project_root()).as_posix(),
        "processed_images_count": len(processed_numbers),
        "processed_images_preview": [f"{num:02d}.jpg" for num in processed_numbers[:10]],
        "run_state_status": run_state.status,
        "selected_mode": run_policy["selected_mode"],
        "effective_mode": run_policy["effective_mode"],
        "split_mode": run_policy["split_mode"],
        "refine_mode": run_policy["refine_mode"],
        "skip_locked_automatic": run_policy["effective_mode"] == "automatic",
        "images_converted_count": run_state.images_converted_count,
        "inserted_images_count": run_state.inserted_images_count,
        "last_image_conversion_ts": run_state.last_image_conversion_ts,
        "last_build_ts": run_state.last_build_ts,
        "active_artifact_filename": run_state.active_artifact_filename,
        "cover_jpg_path_state": run_state.cover_jpg_path,
        "md_status": run_state.md_status,
        "md_generated_at": run_state.md_generated_at,
        "run_build_outputs": run_state.build_outputs or {},
        "canonical_status_db": edition.status,
        "can_normalize_text": edition_status == EditorialEdition.STATUS_INGESTED,
        "can_fix_text": edition_status == EditorialEdition.STATUS_NORMALIZED and normalized_md_path.exists(),
        "can_chunk_from_fixed": edition_status == EditorialEdition.STATUS_FIXED_TEXT and fixed_md_path.exists(),
        "raw_upload_name": edition.raw_upload.name if edition.raw_upload else "",
        "raw_materialized_path": edition.raw_materialized_path,
        "raw_sha256": edition.raw_sha256,
        "normalized_path": _relpath_or_abs(normalized_display_path),
        "normalized_path_is_md": normalized_md_path.exists(),
        "normalized_sha256": _sha256_or_blank(normalized_display_path),
        "fixed_path": _relpath_or_abs(fixed_md_path),
        "fixed_exists": fixed_md_path.exists(),
        "fixed_sha256": _sha256_or_blank(fixed_md_path),
        "fix_run_dir": fix_run_dir,
        "fix_report_status": fix_report_status,
        "fix_report_path": (latest_fix_report or {}).get("report_path", ""),
        "fix_actions": (latest_fix_report or {}).get("actions", {}),
        "truth_path_db": edition.truth_path,
        "truth_sha256_db": edition.truth_sha256,
        "canonical_run_dir_db": edition.canonical_run_dir,
        "canonical_tag_db": edition.canonical_official_tag,
        "freeze_source_path": _relpath_or_abs(freeze_source) if freeze_source else "",
        "can_materialize_raw": bool(edition.raw_upload),
        "can_freeze_canonical": bool(freeze_source),
        "canonical_exists": bool(canonical_info.get("exists")),
        "canonical_active_path": canonical_active_rel,
        "canonical_active_json_path": canonical_json_rel,
        "canonical_mode": canonical_info.get("mode") or "(none)",
        "canonical_status": canonical_info.get("status") or "(none)",
        "canonical_sha256": canonical_info.get("sha256") or "(none)",
        "canonical_size_bytes": canonical_info.get("size_bytes") or 0,
        "canonical_reason": canonical_info.get("reason") or "",
        "fasttrack_ready": bool(canonical_info.get("fasttrack_ready")),
        "latest_translate_run_dir": latest_translate_run_rel,
    }

    return render(request, "pipeline/edition_steps.html", context)


def build_book_md(request, book_code, language):
    if request.method != "POST":
        return redirect("book_edition_list")

    edition = get_object_or_404(
        EditorialEdition,
        work__code=book_code,
        language__code=language,
    )

    run_state = _get_or_create_run_state(edition)
    asset_language = _asset_language_from_request(request, language, run_state)
    save_policy = _resolve_policy_for_state(
        run_state=run_state,
        fallback_selected_mode=run_state.selected_mode or "automatic",
    )
    run_state.save(
        update_fields=[
            "selected_mode",
            "effective_mode",
            "split_mode",
            "refine_mode",
            "updated_at",
        ]
    )
    try:
        result = _run_save_pipeline(
            edition=edition,
            asset_language=asset_language,
            run_state=run_state,
            skip_policy=save_policy,
        )
        if result.get("md_action") == "skipped_up_to_date":
            messages.warning(request, "Arquivo já convertido (MD up-to-date).")
        warn_count = len(result["insert"].get("warnings", []))
        if warn_count:
            messages.warning(request, f"SAVE concluido com {warn_count} warning(s) de insercao.")
        messages.success(
            request,
            "SAVE concluido. "
            f"imagens_convertidas={result['conversion'].get('converted_count', 0)} "
            f"md_action={result.get('md_action')} "
            f"inseridas={result['insert'].get('inserted_images_count', 0)} "
            f"build={result['build_output']}",
        )
    except CommandError as exc:
        messages.error(request, f"Falha no save/build: {exc}")
    except Exception as exc:
        messages.error(request, f"Falha inesperada no save/build: {exc}")

    return redirect("edition_steps", edition_id=edition.id)


@require_POST
def fasttrack_text_to_md(request, book_code: str):
    lang = utils.normalize_lang(request.POST.get("language") or "en")
    lang_db = _project_lang_db_code(lang)

    edition = EditorialEdition.objects.filter(
        work__code=book_code,
        language__code=lang_db,
    ).first()
    if not edition:
        edition = EditorialEdition.objects.filter(work__code=book_code).order_by("id").first()
    if not edition:
        raise Http404(f"Edição não encontrada para {book_code}")

    run_state = _get_or_create_run_state(edition)
    try:
        result = _run_fasttrack_from_canonical(
            edition=edition,
            language=lang,
            run_state=run_state,
        )
    except Exception as exc:
        messages.error(request, f"FastTrack bloqueado/falhou: {exc}")
        return redirect("edition_steps", edition_id=edition.id)

    messages.success(
        request,
        "FastTrack concluido. "
        f"md={_relpath_or_abs(Path(result['md_path']))} "
        f"build_source={_relpath_or_abs(Path(result['build_source_md']))}",
    )
    return redirect("edition_steps", edition_id=edition.id)


@require_POST
def refine_es_mx(request, edition_id: int):
    edition = get_object_or_404(EditorialEdition, id=edition_id)
    stage_policy.POLICY.assert_stage_allowed(edition, "refine")

    lang_code = utils.normalize_lang(edition.language.code)
    if lang_code != "es":
        return JsonResponse(
            {"ok": False, "error": "Refine ES-MX is only available for ES editions."},
            status=400,
        )

    book_code = edition.work.code
    contract_path = _resolve_return_flow_contract(lang_code)
    try:
        merge_path, log = _run_return_flow(contract_path, book_code=book_code)
    except Exception as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=500)

    build_path = _copy_merge_to_build(edition, merge_path, paths.merge_refine_path(edition))

    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
    pipeline_state.current_stage = PipelineStage.REFINED
    pipeline_state.refined_at = timezone.now()
    pipeline_state.core_last_txt_path = str(build_path)
    pipeline_state.last_log = log
    pipeline_state.save(update_fields=["current_stage", "refined_at", "core_last_txt_path", "last_log"])

    return JsonResponse(
        {
            "ok": True,
            "variant": "es_mx",
            "out_path": str(merge_path),
            "build_path": str(build_path),
            "stdout": log,
        }
    )
