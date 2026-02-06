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

from .models import BookEditionTemplate, PipelineJob, TextSnapshot, get_book_md_path
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
    if request.method == "GET":
        languages = Language.objects.filter(is_active=True).order_by("code")
        seals = Seal.objects.filter(is_active=True).order_by("name")
        context = {
            "languages": languages,
            "seals": seals,
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
        return redirect("book_edition_new")

    language_obj, _ = Language.objects.get_or_create(
        code=lang_code,
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

    author_obj = None
    if author_name:
        author_obj, _ = Contributor.objects.get_or_create(
            name=author_name,
            defaults={"role": ContributorRole.AUTHOR},
        )

    year = None
    if year_raw:
        try:
            year = int(year_raw)
        except ValueError:
            messages.warning(request, "Ano inválido; salvando sem ano.")

    work_obj, created = Work.objects.get_or_create(
        code=book_code,
        defaults={
            "title": title or book_code,
            "original_language": language_obj,
            "author": author_obj if author_obj else Contributor.objects.first(),
            "year": year,
        },
    )
    if not created:
        updated = False
        if title and not work_obj.title:
            work_obj.title = title
            updated = True
        if author_obj and not work_obj.author_id:
            work_obj.author = author_obj
            updated = True
        if year and not work_obj.year:
            work_obj.year = year
            updated = True
        if updated:
            work_obj.save()

    existing = EditorialEdition.objects.filter(work=work_obj, language=language_obj).first()
    if existing:
        messages.info(request, "Edição já existe.")
        return redirect("book_edition_list")

    edition = EditorialEdition.objects.create(
        work=work_obj,
        language=language_obj,
        seal=seal_obj,
        title=title or work_obj.title,
        author=author_name or (work_obj.author.name if work_obj.author_id else ""),
        edition_year=year,
    )

    EditionPipeline.objects.get_or_create(edition=edition)
    EditionText.objects.get_or_create(edition=edition)

    raw_file = request.FILES.get("raw_file")
    if raw_file:
        dest = _raw_upload_path(edition, raw_file.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as f:
            for chunk in raw_file.chunks():
                f.write(chunk)
        edition.raw_source_path = dest.as_posix()
        edition.save(update_fields=["raw_source_path"])
        EditionText.objects.filter(edition=edition).update(raw_path=dest.as_posix())

    messages.success(request, "Edição criada.")
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
    script_path = project_root / "gaiden" / "scripts" / "book_0002_prebuild_images.sh"
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


def _legacy_gaiden_merge_path(book_id: int, language: str, stage: str) -> Path | None:
    lang = utils.normalize_lang(language)
    base_dir = (
        Path(settings.BASE_DIR).parent
        / "data"
        / "chunks"
        / f"book_{book_id:04d}"
        / f"refine_{lang}_01"
    )
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
    texts = EditionText.objects.filter(edition=edition).first()
    raw_path = (texts.raw_path if texts else "") or edition.raw_source_path

    def _asset_lang_from_request() -> str:
        raw = (request.POST.get("asset_language") or "").strip()
        return utils.normalize_lang(raw or language)

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
                f"Idioma salvo ({target_language}). Proximo passo.",
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
            md_targets = sorted(build_dir.glob("book.*.pre_qa.md"))
            if not md_targets:
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
                match = re.match(r"^book\.([a-z0-9]+)\.(v\d+)\.pre_qa\.md$", md_path.name, re.IGNORECASE)
                if match:
                    lang = match.group(1).lower()
                    out_path = md_path.with_name(md_path.name.replace(".pre_qa.md", ".pre_edition.md"))
                elif md_path.name.startswith("BOOK.PRE_QA."):
                    lang = md_path.name.split(".", 2)[-1].lower()
                    out_path = md_path.with_name(f"BOOK.PRE_EDITION.{lang}.md")
                else:
                    out_path = paths.pre_edition_md_path(
                        edition,
                        version=paths.md_version(
                            edition,
                            language=language,
                            build_dir=build_dir,
                        ),
                    )
                out_path.write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
                md_transform.insert_page_headlines(out_path, lang=lang)
            messages.success(
                request,
                "Headlines de capitulo inseridos no PRE_EDITION.",
            )
            return redirect("edition_steps", edition_id=edition.id)
        if action == "insert_images":
            target_lang = _asset_lang_from_request()
            build_dir = paths.edition_build_dir_for_language(book_code, target_lang)
            md_targets = sorted(build_dir.glob("book.*.pre_edition.md"))
            if not md_targets:
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
    raw_name = Path(raw_path).name if raw_path else None

    def _status(flag: bool) -> str:
        return "OK" if flag else "falta"

    book_id = _parse_book_id(book_code)
    chunk_count = _count_chunks(book_code)

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
    updated_fields = frontmatter_template.apply_language_defaults_if_empty()
    if created or updated_fields:
        frontmatter_template.save()

    def _resolve_md_source_path(lang: str) -> str:
        build_dir = paths.edition_build_dir_for_language(book_code, lang)
        if not build_dir.exists():
            return ""
        order = [
            p.replace(".txt", "")
            for p in paths.merge_priority_names_for_language(lang, build_dir)
        ]
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

    md_version_default = paths.md_version(
        edition,
        language=md_language_default,
        build_dir=paths.edition_build_dir_for_language(book_code, md_language_default),
    )
    pre_edition_path = paths.pre_edition_md_path(edition, version=md_version_default)
    pre_qa_path = paths.pre_qa_md_path(edition, version=md_version_default)
    qa_path = paths.qa_md_path(edition, version=md_version_default)
    final_md_path = paths.final_md_path(edition, version=md_version_default)
    build_md_path = paths.build_md_path(edition, version=md_version_default)
    epub_path = paths.epub_path(edition)
    pdf_path = paths.pdf_path(edition)
    qa_log_path = paths.qa_log_path(edition)

    legacy_final = paths.edition_build_dir(edition) / "BOOK.MD_FINAL"
    legacy_pre_qa = paths.edition_build_dir(edition) / "BOOK.PRE_QA.md"
    if final_md_path.exists() or legacy_final.exists():
        md_status = "QA_DONE"
    elif pre_qa_path.exists() or legacy_pre_qa.exists():
        md_status = "PRE_QA"
    else:
        md_status = "NONE"

    issues = []
    if qa_log_path.exists():
        try:
            issues = json.loads(qa_log_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            issues = []

    asset_lang = utils.normalize_lang(md_language_default or language)
    images_dir = f"data/images/{book_code}/{asset_lang}"
    inserts_json_path = str(
        paths.edition_build_dir_for_language(book_code, asset_lang) / "inserts.json"
    )

    context = {
        "edition": edition,
        "status": {
            "raw": _status(bool(raw_path)),
            "normalize": _status(bool(pipeline_state.normalized_at)),
            "chunk": _status(bool(pipeline_state.chunked_at)),
            "translate": _status(bool(pipeline_state.translated_at)),
            "refine": _status(bool(pipeline_state.refined_at)),
            "polish": _status(bool(pipeline_state.polished_at)),
        },
        "raw_path": raw_path,
        "raw_name": raw_name,
        "normalized_path": (texts.normalized_path if texts else None),
        "translate_language": pipeline_state.translation_language or language,
        "chunk_count": chunk_count,
        "sync_log": sync_log,
        "md_status": md_status,
        "md_final_path": str(final_md_path) if final_md_path.exists() else (str(legacy_final) if legacy_final.exists() else None),
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
        "frontmatter_locked": frontmatter_locked,
        "md_language_default": md_language_default,
        "md_version_default": md_version_default,
        "md_source_map": md_source_map_json,
        "core_last_txt_path": pipeline_state.core_last_txt_path,
        "cover_filepath": edition.cover_filepath,
        "illustrated_images_dir": images_dir,
        "illustrated_inserts_path": inserts_json_path,
    }

    return render(request, "pipeline/edition_steps.html", context)


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
            from gaiden import ingest, normalize as gaiden_normalize

            texts = EditionText.objects.filter(edition=core_edition).first()
            raw_path_str = (texts.raw_path if texts else "") or core_edition.raw_source_path
            if not raw_path_str:
                raise FileNotFoundError("RAW file not found. Upload it first.")

            raw_path = Path(raw_path_str)
            ext = raw_path.suffix.lstrip(".")
            text = ingest.extract_text_from_file(raw_path, ext)
            if not text:
                raise ValueError("Could not extract text from RAW file.")

            _, core_language = _edition_codes(core_edition)
            if utils.normalize_lang(core_language) != "en":
                raise ValueError("Normalize stage is EN-only (normalize_policy_v1_en).")

            normalized = gaiden_normalize.normalize_text_policy_v1_en(text)
            data_dir = Path(settings.BASE_DIR).parent / "data" / "normalized"
            out_dir = data_dir / book_code / "en"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / "normalized.txt"
            out_path.write_text(normalized, encoding="utf-8")

            texts, _ = EditionText.objects.get_or_create(edition=core_edition)
            texts.raw_text = text
            texts.normalized_text = normalized
            texts.raw_path = str(raw_path)
            texts.normalized_path = str(out_path)
            texts.save()

            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=core_edition)
            if pipeline_state.raw_at is None:
                pipeline_state.raw_at = timezone.now()
            pipeline_state.current_stage = PipelineStage.NORMALIZED
            pipeline_state.normalized_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()

            messages.success(request, f"Normalize OK: {out_path}")

        elif step == "chunk":
            core_edition = _global_core_edition(edition)
            result = chapter_chunks.run_chapter_chunks(core_edition)
            chunks_dir = Path(result["path"])
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=core_edition)
            pipeline_state.current_stage = PipelineStage.CHUNKED
            pipeline_state.chunked_at = timezone.now()
            pipeline_state.last_log = ""
            pipeline_state.save()
            messages.success(request, f"Chunks OK: {chunks_dir}")

        elif step == "translate":
            target_language = utils.normalize_lang(request.POST.get("target_language") or language)
            target_edition = _edition_for_language(edition, target_language)
            stage_policy.POLICY.assert_stage_allowed(target_edition, "translate")
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=target_edition)
            core_edition = _global_core_edition(edition)
            core_book_code, _ = _edition_codes(core_edition)
            core_book_id = _parse_book_id(core_book_code)
            if core_book_id is None:
                raise ValueError("book_code must be like book_0001 to translate.")
            chunks_dir = paths.data_dir() / "chunks" / f"book_{core_book_id:04d}" / "en"
            if not chunks_dir.exists():
                messages.error(request, "Chunks nao encontrados. Rode Chunk antes de traduzir.")
                return redirect("edition_steps", edition_id=edition.id)

            from gaiden.translate import run_translate_with_contract
            contract_path = _select_contract_path(target_language)
            lang_dir = "PT-BR" if target_language == "ptbr" else target_language.upper()
            out_dir = paths.data_dir() / "translated" / f"book_{core_book_id:04d}" / lang_dir
            run_translate_with_contract(
                contract_path,
                chunk_dir_override=chunks_dir,
                out_dir_override=out_dir,
            )
            merged_path = _detect_merged_path(out_dir)
            pipeline_state.current_stage = PipelineStage.TRANSLATED
            pipeline_state.translation_language = target_language
            pipeline_state.translated_at = timezone.now()
            if merged_path:
                pipeline_state.core_last_txt_path = str(merged_path)
            pipeline_state.last_log = f"TRANSLATE_ONLY out={out_dir}"
            pipeline_state.save()
            messages.success(request, f"Translate OK: {merged_path or out_dir}")

        elif step == "return_en":
            target_edition = _edition_for_language(edition, "en")
            stage_policy.POLICY.assert_stage_allowed(target_edition, "refine")
            stage_policy.POLICY.assert_stage_allowed(target_edition, "polish")
            book_id = _parse_book_id(target_edition.work.code)
            if book_id is None:
                raise ValueError("book_code must be like book_0001 to return EN.")
            book_code = f"book_{book_id:04d}"

            split_dir = (
                paths.data_dir()
                / "translated"
                / book_code
                / "EN"
                / "split_chapters_for_refine"
            )
            if not split_dir.exists():
                raise FileNotFoundError(
                    f"Split EN não encontrado: {split_dir}. Rode o split antes."
                )

            project_root = Path(settings.BASE_DIR).parent
            cmd = [
                sys.executable,
                "-m",
                "gaiden.return_en",
                "gaiden/contracts/return_aldebaran_en_2026.json",
                "gaiden/contracts/return_yoda_ming_en_2026.json",
                "--book",
                book_code,
            ]

            result = subprocess.run(
                cmd,
                cwd=str(project_root),
                capture_output=True,
                text=True,
                env=os.environ.copy(),
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"return_en falhou.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
                )

            out_path = (
                project_root
                / "data"
                / "builds"
                / book_code
                / "en"
                / "return"
                / "merge_refine_en.txt"
            )
            if not out_path.exists():
                raise FileNotFoundError(
                    f"merge_refine_en.txt não encontrado: {out_path}"
                )

            build_path = _copy_merge_to_build(
                target_edition,
                out_path,
                paths.merge_refine_path(target_edition),
            )

            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=target_edition)
            pipeline_state.current_stage = PipelineStage.POLISHED
            pipeline_state.refined_at = timezone.now()
            pipeline_state.polished_at = timezone.now()
            pipeline_state.core_last_txt_path = str(build_path)
            pipeline_state.last_log = result.stdout.strip()
            pipeline_state.save(
                update_fields=[
                    "current_stage",
                    "refined_at",
                    "polished_at",
                    "core_last_txt_path",
                    "last_log",
                ]
            )
            messages.success(
                request,
                f"Return EN OK: {out_path}",
            )

        elif step == "refine":
            raise ValueError("Refine por chunks desativado por política (translate-only).")
            stage_policy.POLICY.assert_stage_allowed(edition, "refine")
            lang_code = utils.normalize_lang(edition.language.code)
            if lang_code != "de":
                raise ValueError("Refine disponivel apenas para DE (KAISER->BISMARCK).")

            from .services import refine_de as refine_de_service

            result = refine_de_service.run_refine_de_kaiser_bismarck(edition)
            pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
            pipeline_state.current_stage = PipelineStage.REFINED
            pipeline_state.refined_at = timezone.now()
            pipeline_state.last_log = (
                f"KAISER->BISMARCK chunks={result.chunks} input={result.input_path}"
            )
            pipeline_state.save(update_fields=["current_stage", "refined_at", "last_log"])
            messages.success(request, f"Refine DE OK: {result.output_path}")

        elif step == "polish":
            raise ValueError("Polish por chunks desativado por política (translate-only).")
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
            md_version = request.POST.get("md_version") or None
            target_language = md_language or edition.language.code
            target_edition = _edition_for_language(edition, target_language)
            result = md_transform.run_txt_to_md(
                target_edition,
                language_override=md_language,
                version_override=md_version,
            )
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
            md_version = request.POST.get("md_version") or None

            if target_lang and target_lang != edition.language.code:
                build_dir = paths.edition_build_dir_for_language(book_code, target_lang)
                version = paths.md_version(
                    edition,
                    language=target_lang,
                    override=md_version,
                    build_dir=build_dir,
                )
                base = paths.book_md_basename(target_lang, version)
                candidates = [
                    build_dir / f"{base}.qa.md",
                    build_dir / f"{base}.pre_edition.md",
                    build_dir / f"{base}.pre_qa.md",
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
                final_path = paths.final_md_path(
                    edition,
                    language=target_lang,
                    version=version,
                )
                final_path.parent.mkdir(parents=True, exist_ok=True)
                final_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
                result = {"path": str(final_path), "source": str(source_path)}
            else:
                result = md_quality.approve_md_final(
                    edition,
                    language_override=target_lang,
                    version_override=md_version,
                )
            messages.success(
                request,
                f"MD final saved: {result['path']}",
            )

        elif step == "build":
            target_edition = _target_edition()
            _maybe_sync_book_0002_images(book_code)
            md_version = request.POST.get("md_version") or None
            kdp_mode.build_frontmatter_files(target_edition, paths.data_dir() / "frontmatter")
            merged_path = kdp_mode.build_merged_kdp_source(target_edition, version_override=md_version)
            result = {
                "path": str(paths.build_md_path(target_edition, version=md_version)),
                "merged": str(merged_path),
            }
            messages.success(request, f"Build OK: {result['path']}")

        elif step == "export_epub":
            target_edition = _target_edition()
            _maybe_sync_book_0002_images(book_code)
            md_version = request.POST.get("md_version") or None
            result = {"path": str(kdp_mode.build_epub_for_edition(target_edition, version_override=md_version))}
            messages.success(request, f"EPUB OK: {result['path']}")

        elif step == "export_pdf":
            target_edition = _target_edition()
            md_version = request.POST.get("md_version") or None
            result = {"path": str(kdp_mode.build_print_pdf_for_edition(target_edition, version_override=md_version))}
            messages.success(request, f"PDF OK: {result['path']}")

        elif step == "epubcheck":
            target_edition = _target_edition()
            result = {"path": str(kdp_mode.run_epubcheck_for_edition(target_edition))}
            messages.success(request, f"epubcheck OK: {result['path']}")

        elif step == "gaiden":
            target_lang = _target_lang()
            target_edition = _target_edition()
            _maybe_sync_book_0002_images(book_code)
            md_version = request.POST.get("md_version") or None

            build_dir = (
                paths.edition_build_dir_for_language(book_code, target_lang)
                if target_lang != edition.language.code
                else paths.edition_build_dir(edition)
            )

            md_final = paths.final_md_path(target_edition)
            if not md_final.exists():
                alt_md_final = build_dir / "BOOK.MD_FINAL"
                if alt_md_final.exists():
                    md_final = alt_md_final
                else:
                    raise FileNotFoundError("No BOOK.MD_FINAL found. Run QA + Approve first.")

            build_md = paths.build_md_path(target_edition)
            if not build_md.exists():
                build_result = build_book.run_build(
                    edition,
                    language_override=target_lang if target_lang != edition.language.code else None,
                    version_override=md_version,
                )
                messages.info(request, f"Build auto (legacy): {build_result['path']}")

            epub_result = export_book.run_export_epub(
                edition,
                language_override=target_lang if target_lang != edition.language.code else None,
            )
            messages.success(request, f"EPUB legacy OK: {epub_result['path']}")

            result = kdp_mode.gaiden_build_full_book(target_edition, version_override=md_version)
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

    book_id = _parse_book_id(edition.work.code)
    if book_id is None:
        return JsonResponse(
            {"ok": False, "error": "book_code must contain an id like book_0001."},
            status=400,
        )
    book_code = f"book_{book_id:04d}"

    from gaiden import secrets as gaiden_secrets

    project_root = Path(settings.BASE_DIR).parent
    env = os.environ.copy()
    api_key = gaiden_secrets.get_openai_key()
    if not api_key:
        return JsonResponse(
            {"ok": False, "error": "Missing OPENAI_API_KEY in .gaiden_secrets"},
            status=500,
        )
    env["OPENAI_API_KEY"] = api_key
    cmd = ["node", "scripts/es/run_refine_es_mx_workflow.mjs"]
    result = subprocess.run(
        cmd,
        cwd=str(project_root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        return JsonResponse(
            {"ok": False, "stdout": result.stdout, "stderr": result.stderr},
            status=500,
        )

    out_path = (
        project_root
        / "data"
        / "chunks"
        / book_code
        / "refine_es_01"
        / "refined_es_mx_2025.txt"
    )
    if not out_path.exists():
        return JsonResponse(
            {"ok": False, "error": f"Refined output not found: {out_path}"},
            status=500,
        )

    build_path = _copy_merge_to_build(
        edition,
        out_path,
        paths.merge_refine_path(edition),
    )

    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
    pipeline_state.current_stage = PipelineStage.REFINED
    pipeline_state.refined_at = timezone.now()
    pipeline_state.last_log = result.stdout.strip()
    pipeline_state.save(update_fields=["current_stage", "refined_at", "last_log"])

    return JsonResponse(
        {
            "ok": True,
            "variant": "es_mx",
            "out_path": str(out_path),
            "build_path": str(build_path),
            "stdout": result.stdout,
        }
    )
