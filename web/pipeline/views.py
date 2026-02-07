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
    "en": "gaiden/contracts/return_flow_en_2026.json",
    "es": "gaiden/contracts/return_flow_es_2026.json",
    "ptbr": "gaiden/contracts/return_flow_ptbr_2026.json",
    "de": "gaiden/contracts/return_flow_de_2026.json",
    "fr": "gaiden/contracts/return_flow_fr_2026.json",
    "it": "gaiden/contracts/return_flow_it_2026.json",
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

MAX_RAW_UPLOAD_BYTES = 50 * 1024 * 1024

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
            lang_dir = _runner_lang_dir(lang_code)
            raw_path = data_dir / "raw" / book_code / lang_dir / "source.txt"
            normalized_path = data_dir / "normalized" / book_code / lang_dir / f"{book_code}_{lang_code}_v2.txt"
            normalize_report = data_dir / "normalized" / book_code / lang_dir / "normalize_report.json"
            chunks_manifest = data_dir / "chunks" / book_code / lang_code / "chunks_manifest.json"
            translated_path = None
            split_dir = None
            refine_path = None
            polish_path = None
            epub_exists = False
            if book_id is not None:
                translated_path = data_dir / "translated" / f"book_{book_id:04d}" / lang_dir / f"merge_translate_{lang_dir}.txt"
                split_dir = data_dir / "translated" / f"book_{book_id:04d}" / lang_dir / "split_chapters_for_refine"
                build_dir = data_dir / "builds" / book_code / lang_code
                refine_path = build_dir / "merge_refine.txt"
                polish_path = build_dir / "merge_polish.txt"
                epub_exists = bool(list(build_dir.glob("*.epub"))) if build_dir.exists() else False

            normalize_status = "MISSING"
            normalize_badge = "bad"
            if normalized_path.exists():
                report = _read_json(normalize_report)
                if report and report.get("check_ok") is True:
                    normalize_status = "OK"
                    normalize_badge = "ok"
                elif report and report.get("check_ok") is False:
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
                    "raw_ok": raw_path.exists(),
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
    return _runner_lang_dir(code)


def _project_raw_path(book_code: str, lang_code: str, source_format: str | None = None) -> Path:
    del source_format
    lang_dir = _project_lang_dir(lang_code)
    return _project_root() / "data" / "raw" / book_code / lang_dir / "source.txt"


def _project_raw_status(book_code: str, lang_code: str, source_format: str) -> dict:
    path = _project_raw_path(book_code, lang_code, source_format)
    if not path.exists():
        return {"exists": False, "path": str(path)}
    stat = path.stat()
    return {
        "exists": True,
        "path": str(path),
        "size": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime),
    }


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


def _resolve_normalize_preview_path(book_code: str, lang: str) -> tuple[Path | None, str]:
    data_dir = _project_root() / "data"
    lang_code = utils.normalize_lang(lang)
    lang_dir = _runner_lang_dir(lang_code)
    base_name_v2 = f"{book_code}_{lang_code}_v2.txt"

    preview = data_dir / "normalized" / book_code / lang_dir / "normalize_preview.txt"
    if preview.exists():
        return preview, "PREVIEW"

    normalized = data_dir / "normalized" / book_code / lang_dir / base_name_v2
    if normalized.exists():
        return normalized, "NORMALIZED"

    raw_txt = data_dir / "raw" / book_code / lang_dir / "source.txt"
    if raw_txt.exists():
        return raw_txt, "RAW"

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
        lang_dir = _runner_lang_dir(lang_code)
        normalized_path = data_dir / "normalized" / book_code / lang_dir / f"{book_code}_{lang_code}_v2.txt"
        normalize_report = data_dir / "normalized" / book_code / lang_dir / "normalize_report.json"
        chunks_manifest = data_dir / "chunks" / book_code / lang_code / "chunks_manifest.json"

        normalize_status = "MISSING"
        normalize_badge = "bad"
        if normalized_path.exists():
            report = _read_json(normalize_report)
            if report and report.get("check_ok") is True:
                normalize_status = "OK"
                normalize_badge = "ok"
            elif report and report.get("check_ok") is False:
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
                "code": lang,
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
    report_path = data_dir / "normalized" / canonical / _runner_lang_dir(lang_code) / "normalize_report.json"
    report = _read_json(report_path)
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
                if report and "check_ok" in report:
                    item.normalize_check = "OK" if report.get("check_ok") else "FAIL"
                elif report_path.exists():
                    item.normalize_check = "WARN"
                item.normalize_report_path = str(report_path)

    context = {
        "works": works,
        "languages": languages,
        "selected_book_code": selected_book_code,
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
    mode = (request.POST.get("mode") or "MULTILANGUAGE").upper()

    running = PipelineRun.objects.filter(action=action, status="RUNNING").first()
    if running:
        messages.warning(request, f"Já existe um run em execução (#{running.id}).")
        return redirect("pipeline_runner_matrix_detail", run_id=running.id)

    if not book_codes or not languages:
        messages.error(request, "Selecione ao menos 1 book e 1 idioma.")
        return redirect("pipeline_runner_matrix")

    if action not in {"NORMALIZE", "CHUNK", "TRANSLATE", "SPLIT_FOR_REFINE"}:
        messages.error(request, "Ação inválida no MVP.")
        return redirect("pipeline_runner_matrix")

    if mode not in {"MULTILANGUAGE", "SEQUENTIAL"}:
        messages.error(request, "Modo inválido.")
        return redirect("pipeline_runner_matrix")

    if mode == "MULTILANGUAGE" and len(book_codes) != 1:
        messages.error(request, "Multilanguage mode exige 1 book.")
        return redirect("pipeline_runner_matrix")
    if mode == "SEQUENTIAL" and len(languages) != 1:
        messages.error(request, "Sequential mode exige 1 idioma.")
        return redirect("pipeline_runner_matrix")
    if action in {"NORMALIZE", "CHUNK"}:
        if len(languages) != 1:
            messages.error(request, "Esta ação exige 1 idioma.")
            return redirect("pipeline_runner_matrix")

    skip_existing = request.POST.get("skip_existing") == "on"
    stop_on_error = request.POST.get("stop_on_error") == "on"
    dry_run = request.POST.get("dry_run") == "on"

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
        for lang in languages:
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
    context = {
        "works": works,
        "languages": languages,
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
    mapping = {
        "en": "gaiden/contracts/en_modern_2025.json",
        "es": "gaiden/contracts/en_es_2025.json",
        "ptbr": "gaiden/contracts/en_ptbr_2025.json",
        "de": "gaiden/contracts/en_de_krimi_2025.json",
        "fr": "gaiden/contracts/translate_fr_2026.json",
        "it": "gaiden/contracts/translate_it_2026.json",
    }
    rel = mapping.get(utils.normalize_lang(language))
    if not rel:
        raise ValueError(f"No translate contract for language={language}")
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
    return data_dir / "translated" / f"book_{book_id:04d}" / lang_dir / f"merge_translate_{lang_dir}.txt"


def _runner_normalized_path(book_code: str, lang: str) -> Path:
    data_dir = _project_root() / "data"
    lang_code = utils.normalize_lang(lang)
    lang_dir = _runner_lang_dir(lang_code)
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

    cmd = [sys.executable, "-m", "gaiden.return_splits", str(contract_path)]
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
            f"return_splits falhou.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    if not merge_path.exists():
        raise FileNotFoundError(f"Merge não encontrado: {merge_path}")

    return merge_path, result.stdout.strip()


def _detect_merged_path(out_dir: Path) -> Path | None:
    lang_key = out_dir.name
    merged = out_dir / f"merge_translate_{lang_key}.txt"
    if merged.exists():
        return merged
    alt = out_dir / "merged.txt"
    if alt.exists():
        return alt
    candidates = sorted(out_dir.glob("merged_*.txt"))
    if candidates:
        return candidates[0]
    return None


def _count_chunks(book_code: str) -> int | None:
    book_id = _parse_book_id(book_code)
    if book_id is None:
        return None
    data_dir = Path(settings.BASE_DIR).parent / "data"
    chunks_dir = data_dir / "chunks" / f"book_{book_id:04d}" / "en"
    if not chunks_dir.is_dir():
        return None
    return len(list(chunks_dir.glob("ch_*_chunk_*.txt")))


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
            translated_path = (
                data_dir
                / "translated"
                / f"book_{book_id:04d}"
                / lang_dir
                / f"merge_translate_{lang_dir}.txt"
            )
            split_dir = (
                data_dir
                / "translated"
                / f"book_{book_id:04d}"
                / lang_dir
                / "split_chapters_for_refine"
            )
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
