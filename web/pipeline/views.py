import json
from pathlib import Path
import shutil
from datetime import datetime
import re

from django.conf import settings
from django.core.management import call_command
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from editorial.models import (
    Edition as EditorialEdition,
    EditionPipeline,
    EditionText,
    PipelineStage,
)

from .models import BookEditionTemplate, PipelineJob, TextSnapshot, get_book_md_path
from .services import (
    book_manifest,
    build_book,
    chapter_chunks,
    editorial_split,
    export_book,
    legacy_merges,
    md_quality,
    md_transform,
    miolo_transform,
    paths,
    utils,
)


def pipeline_dashboard(request):
    pipelines = EditionPipeline.objects.select_related("edition__work", "edition__language").order_by("edition__work__code")
    return render(request, "pipeline/dashboard.html", {"pipelines": pipelines})


def pipeline_jobs(request):
    pipelines = EditionPipeline.objects.select_related("edition__work", "edition__language").order_by("-id")
    return render(request, "pipeline/jobs.html", {"pipelines": pipelines})


def book_edition_list(request):
    editions = (
        EditorialEdition.objects.select_related("work", "language", "seal")
        .order_by("work__code", "language__code")
    )
    return render(request, "pipeline/book_edition_list.html", {"editions": editions})


def book_edition_edit(request, book_code=None, language=None):
    messages.info(request, "Edicoes sao criadas via seed_editorial ou admin.")
    return redirect("book_edition_list")


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


def edition_steps(request, edition_id: int):
    edition = get_object_or_404(EditorialEdition, id=edition_id)
    book_code, language = _edition_codes(edition)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_translation_language":
            target_language = utils.normalize_lang(request.POST.get("target_language") or language)
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
            pipeline_state.translation_language = target_language
            pipeline_state.save(update_fields=["translation_language"])
            messages.info(
                request,
                f"Idioma salvo ({target_language}). Refine ou Next Step.",
            )
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
            return redirect(
                f"{reverse('edition_steps', kwargs={'edition_id': edition.id})}#transformacao-editorial"
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
                return redirect("edition_steps", edition_id=edition.id)
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
    sync_log = []

    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
    texts = EditionText.objects.filter(edition=edition).first()
    raw_path = (texts.raw_path if texts else "") or edition.raw_source_path
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

    pre_edition_path = paths.pre_edition_md_path(edition)
    pre_qa_path = paths.pre_qa_md_path(edition)
    qa_path = paths.qa_md_path(edition)
    final_md_path = paths.final_md_path(edition)
    build_md_path = paths.build_md_path(edition)
    epub_path = paths.epub_path(edition)
    pdf_path = paths.pdf_path(edition)
    qa_log_path = paths.qa_log_path(edition)
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

    frontmatter_lang = utils.normalize_lang(
        request.GET.get("frontmatter_lang") or language
    )
    frontmatter_langs = [choice[0] for choice in BookEditionTemplate.LANG_CHOICES]
    if frontmatter_lang not in frontmatter_langs:
        frontmatter_lang = language

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
    updated_fields = frontmatter_template.apply_language_defaults_if_empty()
    if created or updated_fields:
        frontmatter_template.save()

    context = {
        "edition": edition,
        "status": {
            "raw": _status(bool(raw_path)),
            "normalize": _status(bool(pipeline_state.normalized_at)),
            "split": _status(bool(pipeline_state.split_at)),
            "split_by_chapter": _status(bool(split_by_chapter_dir and split_by_chapter_dir.exists())),
            "translate": _status(bool(pipeline_state.translated_at)),
            "refine": _status(bool(pipeline_state.refined_at)),
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
    }

    return render(request, "pipeline/edition_steps.html", context)


def run_edition_step(request, edition_id: int, step: str):
    edition = get_object_or_404(EditorialEdition, id=edition_id)
    book_code, language = _edition_codes(edition)

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
            edition.raw_source_path = str(dest_path)
            edition.save(update_fields=["raw_source_path", "updated_at"])

            texts, _ = EditionText.objects.get_or_create(edition=edition)
            texts.raw_path = str(dest_path)
            texts.save(update_fields=["raw_path", "updated_at"])

            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
            pipeline_state.current_stage = PipelineStage.RAW
            pipeline_state.raw_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()
            messages.success(request, f"RAW saved: {dest_path}")

        elif step == "normalize":
            from gaiden import ingest, normalize as gaiden_normalize

            texts = EditionText.objects.filter(edition=edition).first()
            raw_path_str = (texts.raw_path if texts else "") or edition.raw_source_path
            if not raw_path_str:
                raise FileNotFoundError("RAW file not found. Upload it first.")

            raw_path = Path(raw_path_str)
            ext = raw_path.suffix.lstrip(".")
            text = ingest.extract_text_from_file(raw_path, ext)
            if not text:
                raise ValueError("Could not extract text from RAW file.")

            normalized = gaiden_normalize.normalize_text_v2(text)
            data_dir = Path(settings.BASE_DIR).parent / "data" / "normalized"
            data_dir.mkdir(parents=True, exist_ok=True)
            out_path = data_dir / f"{book_code}_{language}_v2.txt"
            out_path.write_text(normalized, encoding="utf-8")

            texts, _ = EditionText.objects.get_or_create(edition=edition)
            texts.raw_text = text
            texts.normalized_text = normalized
            texts.raw_path = str(raw_path)
            texts.normalized_path = str(out_path)
            texts.save()

            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
            if pipeline_state.raw_at is None:
                pipeline_state.raw_at = timezone.now()
            pipeline_state.current_stage = PipelineStage.NORMALIZED
            pipeline_state.normalized_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()

            messages.success(request, f"Normalize OK: {out_path}")

        elif step == "split":
            count = editorial_split.run_split_struct(edition)
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
            pipeline_state.current_stage = PipelineStage.SPLIT
            pipeline_state.split_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()
            messages.success(request, f"Split struct OK: {count} units")

        elif step == "chunk":
            count = editorial_split.run_split_01(edition)
            book_id = _parse_book_id(book_code)
            chunks_dir = Path("data/chunks") / f"book_{book_id:04d}" / "split_01"
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
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

            target_language = utils.normalize_lang(request.POST.get("target_language") or language)
            contract_path = _select_contract_path(target_language)
            run_translate_with_contract(contract_path)

            out_dir_path = _resolve_contract_out_dir(contract_path, edition)

            merged_path = _detect_merged_path(out_dir_path)
            if not merged_path:
                raise FileNotFoundError(f"Merged translation not found in {out_dir_path}")
            build_path = _copy_merge_to_build(edition, merged_path, paths.merge_translate_path(edition))
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
            pipeline_state.current_stage = PipelineStage.TRANSLATED
            pipeline_state.translation_language = target_language
            pipeline_state.translated_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()
            messages.success(request, "Translate OK")

        elif step == "refine":
            from gaiden.translate import run_translate_with_contract

            contract_path = _select_refine_contract(language)
            run_translate_with_contract(contract_path)

            out_dir_path = _resolve_contract_out_dir(contract_path, edition)

            merged_path = _detect_merged_path(out_dir_path)
            if not merged_path:
                raise FileNotFoundError(f"Merged refine not found in {out_dir_path}")
            build_path = _copy_merge_to_build(edition, merged_path, paths.merge_refine_path(edition))
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
            pipeline_state.current_stage = PipelineStage.REFINED
            pipeline_state.refined_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()
            messages.success(request, "Refine OK")

        elif step == "polish":
            from gaiden.polish_en_2025 import run_polish_en_2025

            book_id = _parse_book_id(book_code)
            if book_id is None:
                raise ValueError("book_code must be like book_0001 to polish.")
            if language != "en":
                raise ValueError("Polish is only available for English.")

            run_polish_en_2025(book_id=book_id, lang_key="en_modern_2025")
            out_path = Path(f"data/chunks/book_{book_id:04d}/refine_en_01/merged_polished_en_2025.txt")
            build_path = _copy_merge_to_build(edition, out_path, paths.merge_polish_path(edition))
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
            pipeline_state.current_stage = PipelineStage.POLISHED
            pipeline_state.polished_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()
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
                    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
                    pipeline_state.current_stage = PipelineStage.MIOLO_MD
                    pipeline_state.miolo_md_at = timezone.now()
                    pipeline_state.last_log = ""
                    pipeline_state.save()
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
        pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
        pipeline_state.last_log = str(exc)
        pipeline_state.save()

    return redirect("edition_steps", edition_id=edition.id)


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
        EditorialEdition,
        work__code=book_code,
        language__code=language,
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


def preview_merge_translate(request, edition_id: int):
    edition = get_object_or_404(EditorialEdition, id=edition_id)
    pipeline_state = EditionPipeline.objects.filter(edition=edition).first()
    book_code, language = _edition_codes(edition)

    target_language = utils.normalize_lang(
        (pipeline_state.translation_language if pipeline_state else None) or language
    )
    contract_path = _select_contract_path(target_language)
    out_dir_path = _resolve_contract_out_dir(contract_path, edition)
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
    contract_path = _select_contract_path(target_language)
    out_dir_path = _resolve_contract_out_dir(contract_path, edition)
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
