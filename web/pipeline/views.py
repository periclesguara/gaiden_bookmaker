import json
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

from .models import (
    BookEditionTemplate,
    PipelineJob,
    PipelineRun,
    PipelineRunItem,
    TextSnapshot,
    get_book_md_path,
)
from .services import (
    book_manifest,
    build_book,
    chapter_chunks,
    export_book,
    legacy_merges,
    md_quality,
    md_transform,
    miolo_transform,
    paths,
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
    {"code": "en_modern", "label": "EN Modern (2026)"},
    {"code": "en_2026", "label": "EN 2026 (alias → en_modern)"},
    {"code": "de", "label": "DE"},
    {"code": "fr", "label": "FR"},
    {"code": "es", "label": "ES"},
    {"code": "ptbr", "label": "PT-BR"},
    {"code": "it", "label": "IT"},
]
TRANSLATE_TARGET_CODES = {t["code"] for t in TRANSLATE_TARGETS}
TRANSLATE_LEGACY_BOOKS = {"book_0001", "book_0002"}

MAX_RAW_UPLOAD_BYTES = 50 * 1024 * 1024

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

        dest_path = _project_raw_path(book_code, base_language, source_format)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
        with tmp_path.open("wb+") as dest:
            for chunk in upload.chunks():
                dest.write(chunk)
        tmp_path.replace(dest_path)

        base_edition.raw_source_path = str(dest_path)
        base_edition.save(update_fields=["raw_source_path", "updated_at"])
        text, _ = EditionText.objects.get_or_create(edition=base_edition)
        text.raw_path = str(dest_path)
        text.save(update_fields=["raw_path", "updated_at"])

        messages.success(request, "Projeto criado e RAW salvo.")
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

        dest_path = _project_raw_path(book_code, lang_code, work.source_format)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")

        with tmp_path.open("wb+") as dest:
            for chunk in upload.chunks():
                dest.write(chunk)
        tmp_path.replace(dest_path)

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
        edition.raw_source_path = str(dest_path)
        edition.save(update_fields=["raw_source_path", "updated_at"])
        texts, _ = EditionText.objects.get_or_create(edition=edition)
        texts.raw_path = str(dest_path)
        texts.save(update_fields=["raw_path", "updated_at"])

        messages.success(request, f"RAW salvo em {dest_path}")
        return redirect("projects_upload_raw", book_code=book_code, language=language)

    raw_status = _project_raw_status(book_code, lang_code, work.source_format)
    context = {
        "work": work,
        "language": lang_code,
        "raw_status": raw_status,
        "expected_ext": "txt" if work.source_format.upper() == "TXT" else "md",
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

    context = {
        "works": works,
        "languages": languages,
        "selected_book_code": selected_book_code,
        "selected_books": selected_books,
        "selected_languages": selected_languages,
        "pipeline_mode": session_mode,
        "default_langs": default_langs,
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

    if action in {"TRANSLATE", "TRANSLATE_DEFAULT"} and not languages:
        messages.error(request, "Selecione ao menos 1 idioma.")
        return redirect("pipeline_runner_matrix")

    if action not in {"NORMALIZE", "CHUNK", "TRANSLATE", "TRANSLATE_DEFAULT", "SPLIT_FOR_REFINE"}:
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

    run_languages = languages
    if action in {"NORMALIZE", "CHUNK"}:
        run_languages = ["en"]

    run = PipelineRun.objects.create(
        mode="MATRIX",
        action=action,
        options={
            "queue_mode": True,
            "skip_existing": skip_existing,
            "stop_on_error": stop_on_error,
            "dry_run": dry_run,
            "mode": mode,
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

    context = {
        "works": works,
        "languages": languages,
        "selected_book_code": "",
        "selected_books": session_books,
        "selected_languages": session_langs,
        "pipeline_mode": session_mode,
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
    norm = utils.normalize_lang(lang)
    if norm == "ptbr":
        return "PT-BR"
    return norm.upper()


def _runner_merge_translate_path(book_id: int, lang: str) -> Path:
    data_dir = _project_root() / "data"
    lang_dir = _runner_lang_dir(lang)
    return data_dir / "translated" / f"book_{book_id:04d}" / lang_dir / "merge_refine_clean.txt"


def _runner_normalized_path(book_code: str, lang: str) -> Path:
    data_dir = _project_root() / "data"
    lang_code = utils.normalize_lang(lang)
    lang_dir = _fs_lang_dir(lang_code)
    return data_dir / "normalized" / book_code / lang_dir / f"{book_code}_{lang_code}_v2.txt"


def _runner_split_dir_path(book_id: int, lang: str) -> Path:
    data_dir = _project_root() / "data"
    lang_dir = _runner_lang_dir(lang)
    return data_dir / "translated" / f"book_{book_id:04d}" / lang_dir / "split_chapters_for_refine"


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
    candidates = [
        out_dir / "merge_refine_clean.txt",
        out_dir / f"merge_translate_{lang_key}.txt",
        out_dir / f"{book_code}_{lang_key}_merged_v1.txt",
        out_dir / "merged.txt",
    ]
    for path in candidates:
        if path.exists():
            return path
    extras = sorted(out_dir.glob("merged_*.txt"))
    if extras:
        return extras[0]
    return None


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
        "selected_targets": ["en_modern"],
        "selected_queue": [],
        "selected_target_lang": "en_modern",
        "run_output": None,
        "contract_path": None,
        "run_flags": {"dry_run": True, "resume": True, "fail_fast": True},
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
    context["run_flags"] = {"dry_run": dry_run, "resume": resume, "fail_fast": fail_fast}

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
        targets = [normalize_lang_code(t, default="en_modern") for t in targets]
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
            merged = _detect_merged_path(out_dir) or (out_dir / "merge_refine_clean.txt")
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
        target_lang = "en_modern"
    target_lang = normalize_lang_code(target_lang, default="en_modern")
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
        merged = _detect_merged_path(out_dir) or (out_dir / "merge_refine_clean.txt")
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

    def _asset_lang_from_request() -> str:
        raw = (request.POST.get("asset_language") or "").strip()
        return utils.normalize_lang(raw or language)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "upload_cover":
            cover_file = request.FILES.get("cover_file")
            if not cover_file:
                messages.error(request, "Selecione uma imagem de capa.")
                return redirect("edition_steps", edition_id=edition.id)
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
            return redirect("edition_steps", edition_id=edition.id)
        if action == "upload_images_zip":
            images_zip = request.FILES.get("images_zip")
            if not images_zip:
                messages.error(request, "Selecione um ZIP de imagens.")
                return redirect("edition_steps", edition_id=edition.id)

            lang_code = _asset_lang_from_request()
            images_base = (
                Path(settings.BASE_DIR).parent
                / "data"
                / "images"
                / book_code
                / lang_code
            )

            allowed_exts = {".png", ".webp", ".tif", ".tiff", ".bmp", ".jpg", ".jpeg"}
            chapter_re = re.compile(r"^\d{2}$")
            extracted: list[tuple[zipfile.ZipInfo, Path]] = []
            invalid: list[str] = []
            seen: set[str] = set()

            try:
                with zipfile.ZipFile(images_zip) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        name = info.filename
                        if "__MACOSX" in name or name.endswith(".DS_Store") or name.startswith("."):
                            continue
                        if name.startswith("/") or ".." in Path(name).parts:
                            invalid.append(name)
                            continue
                        parts = [p for p in Path(name).parts if p not in ("", ".")]
                        filename = parts[-1]
                        folder = ""
                        idx = next((i for i, p in enumerate(parts) if chapter_re.match(p)), None)
                        if idx is not None and idx == len(parts) - 2:
                            folder = parts[idx]
                        elif len(parts) == 1:
                            stem = Path(filename).stem
                            match = re.match(r"^(\d{2})", stem)
                            if match:
                                folder = match.group(1)
                        if not folder or not chapter_re.match(folder):
                            invalid.append(name)
                            continue
                        ext = Path(filename).suffix.lower()
                        if ext not in allowed_exts:
                            invalid.append(name)
                            continue
                        dest_rel = Path(folder) / filename
                        if dest_rel.as_posix() in seen:
                            invalid.append(name)
                            continue
                        seen.add(dest_rel.as_posix())
                        extracted.append((info, dest_rel))
            except zipfile.BadZipFile:
                messages.error(request, "ZIP invalido.")
                return redirect("edition_steps", edition_id=edition.id)

            if invalid:
                preview = ", ".join(invalid[:5])
                messages.error(
                    request,
                    f"ZIP contem arquivos invalidos (ex: {preview}). Use pastas 00..NN ou nomes 00..NN.*.",
                )
                return redirect("edition_steps", edition_id=edition.id)

            if not extracted:
                messages.error(request, "ZIP nao contem imagens validas.")
                return redirect("edition_steps", edition_id=edition.id)

            if images_base.exists():
                shutil.rmtree(images_base)
            images_base.mkdir(parents=True, exist_ok=True)

            images_zip.seek(0)
            with zipfile.ZipFile(images_zip) as zf:
                for info, dest_rel in extracted:
                    dest_path = images_base / dest_rel
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, dest_path.open("wb") as dst:
                        shutil.copyfileobj(src, dst)

            inserts_path = (
                paths.edition_build_dir_for_language(book_code, lang_code) / "inserts.json"
            )
            inserts_data = {}
            if inserts_path.exists():
                try:
                    inserts_data = json.loads(inserts_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    inserts_data = {}
            inserts_data["image_dir"] = f"data/images/{book_code}/{lang_code}"
            inserts_path.parent.mkdir(parents=True, exist_ok=True)
            inserts_path.write_text(json.dumps(inserts_data, indent=2), encoding="utf-8")

            try:
                from .services.inserts import normalize_image_dir

                normalize_image_dir(images_base)
            except Exception as exc:
                messages.error(request, f"Falha ao normalizar imagens: {exc}")
                return redirect("edition_steps", edition_id=edition.id)

            messages.success(
                request,
                f"Imagens extraidas em {inserts_data['image_dir']} e inserts.json atualizado.",
            )
            return redirect("edition_steps", edition_id=edition.id)
        if action == "upload_images_individual":
            files = request.FILES.getlist("images_files")
            folder_input = (request.POST.get("images_folder") or "").strip()
            if not files:
                messages.error(request, "Selecione uma ou mais imagens.")
                return redirect("edition_steps", edition_id=edition.id)
            if folder_input and not re.match(r"^\d{2}$", folder_input):
                messages.error(request, "Pasta invalida. Use 00..NN ou deixe vazio.")
                return redirect("edition_steps", edition_id=edition.id)

            lang_code = _asset_lang_from_request()
            images_base = (
                Path(settings.BASE_DIR).parent
                / "data"
                / "images"
                / book_code
                / lang_code
            )
            allowed_exts = {".png", ".webp", ".tif", ".tiff", ".bmp", ".jpg", ".jpeg"}
            saved = 0
            saved_folders: set[str] = set()
            for upload in files:
                name = Path(upload.name).name
                ext = Path(name).suffix.lower()
                if ext not in allowed_exts:
                    messages.error(request, f"Formato invalido: {name}")
                    return redirect("edition_steps", edition_id=edition.id)
                folder = folder_input
                if not folder:
                    stem = Path(name).stem
                    match = re.match(r"^(\d{2})", stem)
                    if match:
                        folder = match.group(1)
                if not folder or not re.match(r"^\d{2}$", folder):
                    messages.error(
                        request,
                        f"Nome invalido: {name}. Use prefixo 00..NN no arquivo ou selecione a pasta.",
                    )
                    return redirect("edition_steps", edition_id=edition.id)
                target_dir = images_base / folder
                target_dir.mkdir(parents=True, exist_ok=True)
                dest_path = target_dir / name
                if dest_path.exists():
                    messages.error(request, f"Arquivo ja existe: {dest_path.name}")
                    return redirect("edition_steps", edition_id=edition.id)
                with dest_path.open("wb+") as dest:
                    for chunk in upload.chunks():
                        dest.write(chunk)
                saved += 1
                saved_folders.add(folder)

            inserts_path = (
                paths.edition_build_dir_for_language(book_code, lang_code) / "inserts.json"
            )
            inserts_data = {}
            if inserts_path.exists():
                try:
                    inserts_data = json.loads(inserts_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    inserts_data = {}
            inserts_data["image_dir"] = f"data/images/{book_code}/{lang_code}"
            inserts_path.parent.mkdir(parents=True, exist_ok=True)
            inserts_path.write_text(json.dumps(inserts_data, indent=2), encoding="utf-8")

            try:
                from .services.inserts import normalize_image_dir

                normalize_image_dir(images_base)
            except Exception as exc:
                messages.error(request, f"Falha ao normalizar imagens: {exc}")
                return redirect("edition_steps", edition_id=edition.id)

            if len(saved_folders) == 1:
                folder_suffix = f"/{next(iter(saved_folders))}"
            else:
                folder_suffix = ""
            messages.success(
                request,
                f"{saved} imagem(ns) salvas em {inserts_data['image_dir']}{folder_suffix}.",
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
            translated_ok = translated_path.exists()
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

    asset_lang_default = utils.normalize_lang(language)
    images_dir = f"data/images/{book_code}/{asset_lang_default}"
    inserts_json_path = str(
        paths.edition_build_dir_for_language(book_code, asset_lang_default) / "inserts.json"
    )

    context = {
        "edition": edition,
        "book_code": book_code,
        "language": language,
        "languages": languages,
        "status_rows": status_rows,
        "cover_filepath": edition.cover_filepath,
        "illustrated_images_dir": images_dir,
        "illustrated_inserts_path": inserts_json_path,
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

    call_command(
        "build_book_text",
        book_code=edition.work.code,
        language=edition.language.code,
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
