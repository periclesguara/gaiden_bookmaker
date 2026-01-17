import json
import os
import sqlite3
import zipfile
from pathlib import Path
import shutil
from datetime import datetime
import re

from django.conf import settings
from django.core.management import call_command
from django.contrib import messages
from django.db.models import Case, Count, IntegerField, Q, When
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import BookEditionTemplateForm
from .models import BookEditionTemplate, PipelineJob, get_book_md_path
from .services import (
    book_manifest,
    build_book,
    export_book,
    legacy_merges,
    md_quality,
    md_transform,
    miolo_transform,
    paths,
    text_source,
    utils,
)


DB_PATH = Path(settings.BASE_DIR).parent / "data" / "db" / "gaiden.sqlite3"


def _badge(flag: bool) -> str:
    return "✅" if flag else "—"


def pipeline_dashboard(request):
    # Conecta no SQLite do Gaiden
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Em vez de depender da tabela books, usamos o que EXISTE:
    # os book_id reais da book_translated_merged
    try:
        cur.execute(
            """
            SELECT DISTINCT book_id AS id
              FROM book_translated_merged
             ORDER BY book_id;
            """
        )
        books = cur.fetchall()
    except sqlite3.Error as e:
        conn.close()
        return HttpResponse(
            f"<h1>Gaiden Pipeline</h1><p>Erro acessando DB: {e}</p>",
            content_type="text/html",
        )

    books_data = []

    for book in books:
        book_id = book["id"]

        # translated
        cur.execute(
            """
            SELECT lang_key
              FROM book_translated_merged
             WHERE book_id = ?
            """,
            (book_id,),
        )
        translated_langs = [row["lang_key"] for row in cur.fetchall()]

        # refined
        try:
            cur.execute(
                """
                SELECT lang, variant
                  FROM book_refined_merged
                 WHERE book_id = ?
                """,
                (book_id,),
            )
            refined_rows = cur.fetchall()
        except sqlite3.OperationalError:
            refined_rows = []

        refined_langs = [row["lang"] for row in refined_rows]

        # polished
        try:
            cur.execute(
                """
                SELECT lang, variant
                  FROM book_polished_merged
                 WHERE book_id = ?
                """,
                (book_id,),
            )
            polished_rows = cur.fetchall()
        except sqlite3.OperationalError:
            polished_rows = []

        polished_langs = [row["lang"] for row in polished_rows]

        all_langs = sorted(set(translated_langs) | set(refined_langs) | set(polished_langs))

        lang_status = []
        for lang in all_langs:
            lang_status.append(
                {
                    "lang": lang,
                    "translated": lang in translated_langs,
                    "refined": lang in refined_langs,
                    "polished": lang in polished_langs,
                }
            )

        books_data.append(
            {
                "id": book_id,
                "label": f"Book {book_id}",
                "langs": lang_status,
            }
        )

    conn.close()

    parts = []
    parts.append("<html><head><meta charset='utf-8'><title>Gaiden Pipeline</title></head><body>")
    parts.append("<h1>Gaiden Pipeline – Status dos Livros</h1>")

    if not books_data:
        parts.append("<p>Nenhum livro encontrado em <code>book_translated_merged</code>.</p>")
    else:
        for book in books_data:
            parts.append(
                f"<h2>Livro {book['id']}: {book['label']}</h2>"
            )
            parts.append(
                "<table border='1' cellspacing='0' cellpadding='4'>"
                "<tr>"
                "<th>Idioma</th>"
                "<th>Translated</th>"
                "<th>Refined</th>"
                "<th>Polished</th>"
                "</tr>"
            )
            for ls in book["langs"]:
                parts.append(
                    "<tr>"
                    f"<td>{ls['lang']}</td>"
                    f"<td style='text-align:center'>{_badge(ls['translated'])}</td>"
                    f"<td style='text-align:center'>{_badge(ls['refined'])}</td>"
                    f"<td style='text-align:center'>{_badge(ls['polished'])}</td>"
                    "</tr>"
                )
            parts.append("</table>")

    parts.append("</body></html>")

    return HttpResponse("\n".join(parts), content_type="text/html")


def pipeline_dashboard(request):
    books = (
        PipelineJob.objects.values("book_code", "book_title")
        .annotate(
            total_jobs=Count("id"),
            success_jobs=Count("id", filter=Q(status="SUCCESS")),
            fail_jobs=Count("id", filter=Q(status="FAIL")),
        )
        .order_by("book_code")
    )
    return render(request, "pipeline/dashboard.html", {"books": books})


def pipeline_jobs(request):
    stage_order = Case(
        When(stage="raw", then=0),
        When(stage="normalize", then=1),
        When(stage="split", then=2),
        When(stage="translate", then=3),
        When(stage="refine", then=4),
        When(stage="polish", then=5),
        default=99,
        output_field=IntegerField(),
    )

    qs = (
        PipelineJob.objects.annotate(stage_index=stage_order)
        .order_by("book_code", "language", "stage_index")
    )
    book_code = request.GET.get("book")
    if book_code:
        qs = qs.filter(book_code=book_code)
    jobs = list(qs)
    edition_map = {}
    if jobs:
        keys = {(j.book_code, j.language) for j in jobs}
        cond = Q()
        for book_code_value, language_value in keys:
            cond |= Q(book_code=book_code_value, language=language_value)
        editions = BookEditionTemplate.objects.filter(cond) if cond else []
        edition_map = {(e.book_code, e.language): e.id for e in editions}

    for job in jobs:
        job.edition_id = edition_map.get((job.book_code, job.language))

    return render(request, "pipeline/jobs.html", {"jobs": jobs})


def book_edition_list(request):
    editions = BookEditionTemplate.objects.all().order_by("book_code", "language")
    return render(request, "pipeline/book_edition_list.html", {"editions": editions})


def book_edition_edit(request, book_code=None, language=None):
    instance = None

    if book_code and language:
        try:
            instance = BookEditionTemplate.objects.get(book_code=book_code, language=language)
        except BookEditionTemplate.DoesNotExist:
            instance = None

    if request.method == "POST":
        form = BookEditionTemplateForm(request.POST, request.FILES, instance=instance)
        if form.is_valid():
            obj = form.save(commit=False)
            project_root = Path(settings.BASE_DIR).parent
            data_dir = project_root / "data"

            cover_file = form.cleaned_data.get("cover_file")
            if cover_file:
                cover_dir = data_dir / "covers" / obj.book_code / obj.language
                cover_dir.mkdir(parents=True, exist_ok=True)
                cover_path = cover_dir / cover_file.name
                with cover_path.open("wb+") as dest:
                    for chunk in cover_file.chunks():
                        dest.write(chunk)
                obj.cover_filepath = os.path.relpath(cover_path, project_root)

            images_zip = form.cleaned_data.get("images_zip")
            if images_zip:
                images_dir = data_dir / "images" / obj.book_code / obj.language
                images_dir.mkdir(parents=True, exist_ok=True)
                zip_path = images_dir / images_zip.name
                with zip_path.open("wb+") as dest:
                    for chunk in images_zip.chunks():
                        dest.write(chunk)
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(images_dir)
                try:
                    zip_path.unlink()
                except FileNotFoundError:
                    pass
                obj.images_dir = os.path.relpath(images_dir, project_root)

            obj.save()
            uploaded = form.cleaned_data.get("source_file")
            if uploaded:
                raw_base_dir = data_dir / "raw"
                dest_dir = raw_base_dir / obj.book_code
                dest_dir.mkdir(parents=True, exist_ok=True)
                ext = Path(uploaded.name).suffix or ".txt"
                dest_path = dest_dir / f"{obj.book_code}_{obj.language}_raw{ext}"
                with dest_path.open("wb+") as dest:
                    for chunk in uploaded.chunks():
                        dest.write(chunk)
                PipelineJob.objects.update_or_create(
                    book_code=obj.book_code,
                    language=obj.language,
                    stage="raw",
                    defaults={
                        "book_title": obj.title,
                        "status": "SUCCESS",
                        "filepath": str(dest_path),
                        "message": "Raw file uploaded.",
                    },
                )
            return redirect("book_edition_edit", book_code=obj.book_code, language=obj.language)
    else:
        initial = {}
        if book_code:
            initial["book_code"] = book_code
        if language:
            initial["language"] = language
        form = BookEditionTemplateForm(instance=instance, initial=initial)

    return render(
        request,
        "pipeline/book_edition_form.html",
        {"form": form, "instance": instance},
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


def _raw_upload_path(edition, uploaded_name: str) -> Path:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    raw_base_dir = data_dir / "raw"
    dest_dir = raw_base_dir / edition.book_code
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(uploaded_name).suffix or ".txt"
    return dest_dir / f"{edition.book_code}_{edition.language}_raw{ext}"


def _upsert_job(edition, stage: str, status: str, filepath: str, message: str = "") -> None:
    PipelineJob.objects.update_or_create(
        book_code=edition.book_code,
        language=edition.language,
        stage=stage,
        defaults={
            "book_title": edition.title,
            "status": status,
            "filepath": filepath,
            "message": message,
        },
    )


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
    return Path(rel)


def _select_refine_contract(language: str) -> Path:
    mapping = {
        "en": "gaiden/contracts/refine/en_refine_2025.json",
        "es": "gaiden/contracts/refine/es_refine_2025.json",
        "ptbr": "gaiden/contracts/refine/ptbr_refine_2025.json",
    }
    rel = mapping.get(utils.normalize_lang(language))
    if not rel:
        raise ValueError(f"No refine contract for language={language}")
    return Path(rel)


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
        return Path(out_dir)
    book_id = _parse_book_id(edition.book_code)
    if book_id is None:
        raise ValueError("book_code must be like book_0001 to resolve out_dir.")
    target_lang = _contract_target_lang(payload)
    return Path("data/translated") / f"book_{book_id:04d}" / "split_01" / target_lang


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


def _sync_canonical_merges_from_jobs(edition) -> list[str]:
    stage_to_target = {
        "polish": paths.merge_polish_path(edition),
        "refine": paths.merge_refine_path(edition),
        "translate": paths.merge_translate_path(edition),
    }
    synced: list[str] = []
    for stage, target_path in stage_to_target.items():
        if target_path.exists():
            continue
        job = (
            PipelineJob.objects.filter(
                book_code=edition.book_code,
                language=edition.language,
                stage=stage,
                status="SUCCESS",
            )
            .order_by("-updated_at")
            .first()
        )
        source_path = None
        if job and job.filepath:
            source_path = Path(job.filepath)
            if not source_path.exists():
                source_path = None
        if source_path is None:
            book_id = _parse_book_id(edition.book_code)
            if book_id is None:
                continue
            source_path = _legacy_gaiden_merge_path(book_id, edition.language, stage)
            if source_path is None:
                continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target_path)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        synced.append(f"{stamp} | {stage}: {source_path} -> {target_path}")
    return synced


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


def edition_steps(request, edition_id: int):
    edition = get_object_or_404(BookEditionTemplate, id=edition_id)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "set_text_source":
            choices = request.POST.getlist("text_source_choice")
            if not choices:
                mode = "auto"
            else:
                if "auto" in choices:
                    choices = [choice for choice in choices if choice != "auto"]
                mode = "auto" if not choices else "||".join(choices)
            edition.text_source_mode = mode
            edition.save(update_fields=["text_source_mode"])
            messages.success(request, "Fonte de texto atualizada.")
            return redirect("edition_steps", edition_id=edition.id)
        if action == "insert_headlines":
            build_dir = paths.edition_build_dir(edition)
            md_targets = sorted(build_dir.glob("BOOK.PRE_QA*.md"))
            md_targets = [path for path in md_targets if path.is_file()]
            if not md_targets:
                messages.error(
                    request,
                    "BOOK.PRE_QA nao encontrado. Rode TXT -> MD antes de inserir headlines.",
                )
                return redirect("edition_steps", edition_id=edition.id)
            for md_path in md_targets:
                out_path = md_path
                lang = (edition.language or "").lower()
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
            return redirect("edition_steps", edition_id=edition.id)
        if action == "insert_images":
            build_dir = paths.edition_build_dir(edition)
            md_targets = sorted(build_dir.glob("BOOK.PRE_EDITION*"))
            md_targets = [path for path in md_targets if path.is_file()]
            if not md_targets:
                messages.error(
                    request,
                    "BOOK.PRE_EDITION nao encontrado. Rode headlines antes de inserir imagens.",
                )
                return redirect("edition_steps", edition_id=edition.id)
            for md_path in md_targets:
                md_transform.insert_image_placeholders(md_path)
            messages.success(
                request,
                "Placeholders de imagem inseridos no PRE_EDITION.",
            )
            return redirect("edition_steps", edition_id=edition.id)

    legacy_merges.sync_legacy_merges_from_translated(edition)
    sync_log = _sync_canonical_merges_from_jobs(edition)

    jobs = PipelineJob.objects.filter(
        book_code=edition.book_code,
        language=edition.language,
    )
    jobs_by_stage = {job.stage: job for job in jobs}

    def is_success(stage: str) -> bool:
        job = jobs_by_stage.get(stage)
        return bool(job and job.status == "SUCCESS")

    def has_merge(stage: str) -> bool:
        if stage == "polish":
            if paths.merge_polish_path(edition).exists():
                return True
        if stage == "refine":
            if paths.merge_refine_path(edition).exists():
                return True
        if stage == "translate":
            if paths.merge_translate_path(edition).exists():
                return True
        book_id = _parse_book_id(edition.book_code)
        if book_id is None:
            return False
        legacy = _legacy_gaiden_merge_path(book_id, edition.language, stage)
        return bool(legacy and legacy.exists())

    def status_label(stage: str) -> str:
        job = jobs_by_stage.get(stage)
        if not job:
            if has_merge(stage):
                return "done"
            return "not run" if stage == "polish" else "pending"
        if job.status == "SUCCESS":
            return "done" if stage in {"translate", "refine", "polish"} else "OK"
        if job.status == "FAIL":
            return "falha"
        return job.status.lower()

    chunk_count = _count_split_chunks(edition.book_code)

    source_info = text_source.get_effective_text_source(edition)

    pre_edition_path = paths.pre_edition_md_path(edition)
    pre_qa_path = paths.pre_qa_md_path(edition)
    qa_path = paths.qa_md_path(edition)
    final_md_path = paths.final_md_path(edition)
    build_md_path = paths.build_md_path(edition)
    epub_path = paths.epub_path(edition)
    pdf_path = paths.pdf_path(edition)
    qa_log_path = paths.qa_log_path(edition)
    miolo_paths = []
    if source_info.selected_sources:
        for source in source_info.selected_sources:
            if len(source_info.selected_sources) == 1:
                miolo_path = paths.miolo_md_path(edition)
            else:
                miolo_path = paths.edition_build_dir(edition) / f"BOOK.MIOLO.{source.language}.md"
            if miolo_path.exists():
                miolo_paths.append(
                    {
                        "language": source.language,
                        "path": str(miolo_path),
                        "label": miolo_path.name,
                    }
                )
    else:
        miolo_path = paths.miolo_md_path(edition)
        if miolo_path.exists():
            miolo_paths.append(
                {
                    "language": edition.language,
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

    context = {
        "edition": edition,
        "status": {
            "raw": "OK" if is_success("raw") else "falta",
            "normalize": "OK" if is_success("normalize") else "falta",
            "split": "OK" if is_success("split") else "falta",
            "translate": status_label("translate"),
            "refine": status_label("refine"),
            "polish": status_label("polish"),
        },
        "chunk_count": chunk_count,
        "text_source": source_info,
        "sync_log": sync_log,
        "md_status": md_status,
        "md_preview": md_preview,
        "md_pre_edition_path": str(pre_edition_path) if pre_edition_path.exists() else None,
        "md_pre_qa_path": str(pre_qa_path) if pre_qa_path.exists() else None,
        "md_final_path": str(final_md_path) if final_md_path.exists() else None,
        "miolo_paths": miolo_paths,
        "qa_issues": issues,
        "build_status": "DONE" if build_md_path.exists() else "NONE",
        "build_path": str(build_md_path) if build_md_path.exists() else None,
        "epub_path": str(epub_path) if epub_path.exists() else None,
        "pdf_path": str(pdf_path) if pdf_path.exists() else None,
    }

    return render(request, "pipeline/edition_steps.html", context)


def run_edition_step(request, edition_id: int, step: str):
    edition = get_object_or_404(BookEditionTemplate, id=edition_id)

    if request.method != "POST":
        return redirect("edition_steps", edition_id=edition.id)

    try:
        if step == "raw":
            uploaded = request.FILES.get("raw_file")
            if not uploaded:
                raise ValueError("No raw file uploaded.")
            dest_path = _raw_upload_path(edition, uploaded.name)
            with dest_path.open("wb+") as dest:
                for chunk in uploaded.chunks():
                    dest.write(chunk)
            _upsert_job(edition, "raw", "SUCCESS", str(dest_path), "Raw file uploaded.")
            messages.success(request, f"RAW saved: {dest_path}")

        elif step == "normalize":
            from gaiden import db as gaiden_db
            from gaiden import ingest, normalize as gaiden_normalize

            book_id = _parse_book_id(edition.book_code)
            if book_id is None:
                raise ValueError("book_code must be like book_0001 to normalize.")

            raw_job = PipelineJob.objects.filter(
                book_code=edition.book_code,
                language=edition.language,
                stage="raw",
                status="SUCCESS",
            ).order_by("-updated_at").first()
            if not raw_job:
                raise FileNotFoundError("RAW file not found. Upload it first.")

            raw_path = Path(raw_job.filepath)
            ext = raw_path.suffix.lstrip(".")
            text = ingest.extract_text_from_file(raw_path, ext)
            if not text:
                raise ValueError("Could not extract text from RAW file.")

            gaiden_db.upsert_extracted_text(book_id, text)
            normalized = gaiden_normalize.normalize_text_v2(text)
            out_path, _ = gaiden_normalize.write_normalized(book_id, normalized, version="v2")
            gaiden_db.upsert_normalized_text(book_id, normalized, version="v2")

            _upsert_job(edition, "normalize", "SUCCESS", str(out_path), "Normalized text saved.")
            messages.success(request, f"Normalize OK: {out_path}")

        elif step == "split":
            from gaiden.split_struct import run_split_struct

            book_id = _parse_book_id(edition.book_code)
            if book_id is None:
                raise ValueError("book_code must be like book_0001 to split.")

            count = run_split_struct(book_id)
            _upsert_job(
                edition,
                "split",
                "SUCCESS",
                str(Path("data/db/gaiden.sqlite3")),
                f"Split struct items: {count}",
            )
            messages.success(request, f"Split struct OK: {count} units")

        elif step == "chunk":
            from gaiden.split_01 import run_split_01

            book_id = _parse_book_id(edition.book_code)
            if book_id is None:
                raise ValueError("book_code must be like book_0001 to chunk.")

            count = run_split_01(book_id)
            chunks_dir = Path("data/chunks") / f"book_{book_id:04d}" / "split_01"
            _upsert_job(
                edition,
                "split",
                "SUCCESS",
                str(chunks_dir),
                f"Chunks generated: {count}",
            )
            messages.success(request, f"Chunks OK: {count}")

        elif step == "translate":
            from gaiden.translate import run_translate_with_contract

            contract_path = _select_contract_path(edition.language)
            run_translate_with_contract(contract_path)

            out_dir_path = _resolve_contract_out_dir(contract_path, edition)

            merged_path = _detect_merged_path(out_dir_path)
            if not merged_path:
                raise FileNotFoundError(f"Merged translation not found in {out_dir_path}")
            build_path = _copy_merge_to_build(edition, merged_path, paths.merge_translate_path(edition))
            _upsert_job(
                edition,
                "translate",
                "SUCCESS",
                str(build_path),
                "Translation finished.",
            )
            messages.success(request, "Translate OK")

        elif step == "refine":
            from gaiden.translate import run_translate_with_contract

            contract_path = _select_refine_contract(edition.language)
            run_translate_with_contract(contract_path)

            out_dir_path = _resolve_contract_out_dir(contract_path, edition)

            merged_path = _detect_merged_path(out_dir_path)
            if not merged_path:
                raise FileNotFoundError(f"Merged refine not found in {out_dir_path}")
            build_path = _copy_merge_to_build(edition, merged_path, paths.merge_refine_path(edition))
            _upsert_job(
                edition,
                "refine",
                "SUCCESS",
                str(build_path),
                "Refine finished.",
            )
            messages.success(request, "Refine OK")

        elif step == "polish":
            from gaiden.polish_en_2025 import run_polish_en_2025

            book_id = _parse_book_id(edition.book_code)
            if book_id is None:
                raise ValueError("book_code must be like book_0001 to polish.")
            if edition.language != "en":
                raise ValueError("Polish is only available for English.")

            run_polish_en_2025(book_id=book_id, lang_key="en_modern_2025")
            out_path = Path(f"data/chunks/book_{book_id:04d}/refine_en_01/merged_polished_en_2025.txt")
            build_path = _copy_merge_to_build(edition, out_path, paths.merge_polish_path(edition))
            _upsert_job(
                edition,
                "polish",
                "SUCCESS",
                str(build_path),
                "Polish finished.",
            )
            messages.success(request, "Polish OK")

        elif step == "txt_to_md":
            result = md_transform.run_txt_to_md(edition)
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
            result = miolo_transform.run_txt_to_miolo(edition)
            items = result.get("items") or []
            if len(items) > 1:
                outputs = ", ".join(f"{item['language']}: {item['path']}" for item in items)
                msg = f"TXT to Miolo OK: {outputs}"
            else:
                msg = f"TXT to Miolo OK: {result['path']}"
                if result.get("path"):
                    _upsert_job(edition, "miolo_md", "SUCCESS", result["path"], "Miolo MD generated.")
            messages.success(request, msg)

        elif step == "qa":
            messages.warning(request, "QA suspenso no momento.")

        elif step == "approve_md":
            result = md_quality.approve_md_final(edition)
            messages.success(
                request,
                f"MD final saved: {result['path']}",
            )

        elif step == "build":
            result = build_book.run_build(edition)
            messages.success(request, f"Build OK: {result['path']}")

        elif step == "export_epub":
            result = export_book.run_export_epub(edition)
            messages.success(request, f"EPUB OK: {result['path']}")

        elif step == "export_pdf":
            result = export_book.run_export_pdf(edition)
            messages.success(request, f"PDF OK: {result['path']}")

        elif step == "epubcheck":
            result = export_book.run_epubcheck(edition)
            messages.success(request, f"epubcheck OK: {result['path']}")

        elif step == "gaiden":
            md_final = paths.final_md_path(edition)
            if not md_final.exists():
                raise FileNotFoundError("No BOOK.MD_FINAL found. Run QA + Approve first.")

            build_md = paths.build_md_path(edition)
            if not build_md.exists():
                build_result = build_book.run_build(edition)
                messages.info(request, f"Build auto: {build_result['path']}")

            epub_result = export_book.run_export_epub(edition)
            messages.success(request, f"EPUB OK: {epub_result['path']}")

            export_user = (
                request.user.username if getattr(request, "user", None) and request.user.is_authenticated else "system"
            )
            manifest = book_manifest.build_manifest(
                edition=edition,
                export_user=export_user,
                epubcheck_status="unknown",
            )
            manifest_path = book_manifest.write_manifest(edition, manifest)
            messages.success(request, f"Manifest saved: {manifest_path}")

        else:
            messages.error(request, f"Unknown step: {step}")

    except Exception as exc:
        messages.error(request, f"Step {step} failed: {exc}")
        if step in {"raw", "normalize", "split", "chunk", "translate", "refine", "polish"}:
            _upsert_job(edition, step if step != "chunk" else "split", "FAIL", "", str(exc))

    return redirect("edition_steps", edition_id=edition.id)


def build_book_md(request, book_code, language):
    if request.method != "POST":
        return redirect("book_edition_edit", book_code=book_code, language=language)

    edition = get_object_or_404(
        BookEditionTemplate,
        book_code=book_code,
        language=language,
    )

    call_command(
        "build_book_text",
        book_code=edition.book_code,
        language=edition.language,
    )

    return redirect("preview_book_md", book_code=book_code, language=language)


def preview_book_md(request, book_code, language):
    path = get_book_md_path(book_code, language)
    if not path.exists():
        raise Http404(f"Markdown file not found: {path}")

    content = path.read_text(encoding="utf-8")
    context = {
        "book_code": book_code,
        "language": language,
        "md_path": str(path),
        "content": content,
    }
    return render(request, "pipeline/preview_md.html", context)


def preview_pre_edition_md(request, book_code, language):
    edition = get_object_or_404(
        BookEditionTemplate,
        book_code=book_code,
        language=language,
    )
    build_dir = paths.edition_build_dir(edition)
    candidates = [
        build_dir / f"BOOK.PRE_EDITION.{language}.md",
        build_dir / f"BOOK.PRE_QA.{language}.md",
        paths.pre_edition_md_path(edition),
        paths.pre_qa_md_path(edition),
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


def preview_miolo_md(request, book_code, language):
    edition = get_object_or_404(
        BookEditionTemplate,
        book_code=book_code,
        language=language,
    )
    build_dir = paths.edition_build_dir(edition)
    candidates = [
        build_dir / f"BOOK.MIOLO.{language}.md",
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
