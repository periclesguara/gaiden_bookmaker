from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST
from pathlib import Path
from django.utils import timezone

from editorial.models import Edition as EditorialEdition, EditionBuild, EditionPipeline
from gaiden_portal.forms import EditionForm
from gaiden_portal.utils import (
    country_for_language,
    get_frontispiece_template_for_edition,
    get_section_template_for_language,
)
from pipeline.models import BookEditionTemplate, LANGUAGE_DEFAULT_TEMPLATES, PROJECT_ROOT
from pipeline.services import utils
from editorial.frontmatter import (
    build_frontmatter_files,
    build_frontmatter_sections,
    merge_frontmatter_sections,
    optional_section_warnings,
)
from editorial import kdp_mode
from .forms import FrontmatterTemplateForm


BOOK_LANGUAGE_DEFAULTS = {
    "book01_the_adventures_of_sherlock_holmes": {
        "en": {
            "title": "The Adventures of Sherlock Holmes",
            "subtitle": "Modern English Edition",
            "author_name": "Arthur Conan Doyle",
            "adapter_name": "Hans Herman Ironside",
        },
        "de": {
            "title": "Die Abenteuer des Sherlock Holmes",
            "subtitle": "Moderne deutsche Ausgabe",
            "author_name": "Arthur Conan Doyle",
            "adapter_name": "Hans Herman Ironside",
            "about_edition_text": (
                "Die Abenteuer des Sherlock Holmes gehoeren zu den einflussreichsten "
                "Detektivgeschichten der Weltliteratur. In diesen Erzaehlungen nimmt uns "
                "Arthur Conan Doyle an die Hand und fuehrt uns durch den Nebel des viktorianischen "
                "Londons, vorbei an verschlossenen Tueren, kryptischen Hinweisen und menschlichen "
                "Geheimnissen.\n\n"
                "Diese moderne Ausgabe bewahrt die Eleganz und Raffinesse des Originals, praesentiert "
                "jedoch eine klare und zugaengliche Sprache, die heutigen Lesern erlaubt, Holmes' "
                "Brillanz und Watsons humorvolle Beobachtungen intensiver zu erleben. Der Rhythmus ist "
                "praeziser, die Dialoge wirken frischer, und die Spannung entfaltet sich auf natuerliche "
                "Weise — so, als stuenden wir selbst im Wohnzimmer der Baker Street 221B.\n\n"
                "Die Sammlung zeigt Sherlock Holmes auf dem Hoehepunkt seiner analytischen Faehigkeiten: "
                "genial, eigenwillig, manchmal ironisch, immer kompromisslos im Streben nach Wahrheit. "
                "Dr. Watson fungiert dabei als Spiegel des Lesers — neugierig, staunend, gelegentlich "
                "skeptisch — und macht die Erzaehlungen nicht nur spannend, sondern zutiefst menschlich.\n\n"
                "Fuer Leser, die zum ersten Mal in die Welt von Holmes eintauchen, ist diese Ausgabe ein "
                "idealer Einstieg. Fuer Kenner ist sie eine frische Rueckkehr zu einer Ikone der Literatur, "
                "die das Genre des Kriminalromans bis heute praegt."
            ),
        },
        "ptbr": {
            "title": "As Aventuras de Sherlock Holmes",
            "subtitle": "Edição em Português Moderno",
            "author_name": "Arthur Conan Doyle",
            "adapter_name": "Hans Herman Ironside",
        },
        "es": {
            "title": "Las Aventuras de Sherlock Holmes",
            "subtitle": "Edición en Español Moderno",
            "author_name": "Arthur Conan Doyle",
            "adapter_name": "Hans Herman Ironside",
        },
    }
}


def _frontmatter_overrides(book_code: str, language: str) -> dict:
    return BOOK_LANGUAGE_DEFAULTS.get(book_code, {}).get(language, {})


def _auto_value_match(value: str, candidates: list[str]) -> bool:
    return bool(value) and value in candidates


def _default_country(language: str) -> str:
    return {
        "en": "Brazil",
        "ptbr": "Brasil",
        "es": "Brasil",
        "de": "Brasilien",
    }.get(language, "Brasil")


def _sync_template_to_edition(template: BookEditionTemplate, edition: EditorialEdition) -> None:
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
    if template.imprint_name and not edition.publisher:
        edition.publisher = template.imprint_name
    edition.frontispiece_template = template.frontispiece_text
    edition.copyright_template = template.copyright_text
    edition.about_edition_template = template.about_edition_text
    edition.about_contributor_template = template.about_contributor_text
    edition.save(
        update_fields=[
            "title",
            "subtitle",
            "author",
            "adapter",
            "translator",
            "editor",
            "publication_year",
            "city",
            "country",
            "imprint_name",
            "seal_name",
            "publisher",
            "frontispiece_template",
            "copyright_template",
            "about_edition_template",
            "about_contributor_template",
        ]
    )


def _write_frontmatter_files(edition: EditorialEdition) -> None:
    base_dir = PROJECT_ROOT / "data" / "frontmatter"
    build_frontmatter_files(edition, base_dir)


def _mark_editorial_changed(edition: EditorialEdition) -> None:
    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
    pipeline_state.editorial_changed = True
    pipeline_state.build_outdated = True
    pipeline_state.last_editorial_update_at = timezone.now()
    pipeline_state.save(
        update_fields=[
            "editorial_changed",
            "build_outdated",
            "last_editorial_update_at",
        ]
    )


def _next_build_version(edition: EditorialEdition, language_code: str) -> int:
    latest = (
        EditionBuild.objects.filter(edition=edition, language_code=language_code)
        .order_by("-build_version")
        .first()
    )
    return 1 if latest is None else latest.build_version + 1


def _archive_build_file(path: Path, version: int, suffix_label: str) -> str:
    if not path.exists():
        return ""
    archive_dir = path.parent / "history"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{path.stem}.v{version}.{suffix_label}{path.suffix}"
    archive_path.write_bytes(path.read_bytes())
    return str(archive_path)


def record_edition_build(
    edition: EditorialEdition,
    *,
    language_code: str,
    build_path: Path | None = None,
    epub_path: Path | None = None,
    pdf_path: Path | None = None,
    notes: str = "",
) -> EditionBuild:
    latest = (
        EditionBuild.objects.filter(edition=edition, language_code=language_code)
        .order_by("-build_version")
        .first()
    )
    if latest and latest.build_path and latest.epub_path == "" and latest.pdf_path == "":
        record = latest
    else:
        version = _next_build_version(edition, language_code)
        record = EditionBuild.objects.create(
            edition=edition,
            language_code=language_code,
            build_version=version,
            build_type=EditionBuild.BUILD_TYPE_INITIAL if version == 1 else EditionBuild.BUILD_TYPE_REBUILD,
        )

    if build_path is not None:
        record.build_path = _archive_build_file(build_path, record.build_version, "build")
    if epub_path is not None:
        record.epub_path = _archive_build_file(epub_path, record.build_version, "epub")
    if pdf_path is not None:
        record.pdf_path = _archive_build_file(pdf_path, record.build_version, "pdf")
    if notes:
        record.notes = notes
    record.save()

    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
    pipeline_state.editorial_changed = False
    pipeline_state.build_outdated = False
    pipeline_state.last_built_at = timezone.now()
    pipeline_state.save(update_fields=["editorial_changed", "build_outdated", "last_built_at"])
    return record


def _frontmatter_files_exist(book_code: str, language: str) -> bool:
    out_dir = PROJECT_ROOT / "data" / "frontmatter" / book_code / language
    return any((out_dir / name).exists() for name in ("frontispiece.md", "copyright.md", "about_this_book.md"))


def frontmatter_template_edit(request, book_code: str, language: str):
    force_defaults = request.GET.get("apply_defaults") == "1"
    edition = (
        EditorialEdition.objects.select_related("work", "language", "seal", "main_contributor")
        .filter(work__code=book_code, language__code=language)
        .first()
    )

    overrides = _frontmatter_overrides(book_code, language)
    country_name = _default_country(language)

    if edition:
        author_name = edition.work.author.name
        contributor_name = edition.main_contributor.name if edition.main_contributor else author_name
        defaults = {
            "title": overrides.get("title", edition.work.title),
            "subtitle": overrides.get("subtitle", ""),
            "author_name": overrides.get("author_name", author_name),
            "publication_year": edition.edition_year or edition.work.year or 2026,
            "imprint_name": edition.seal.name,
            "city_name": "Rio de Janeiro",
            "country_name": country_name,
            "collection_name": "",
            "collaborator_name": contributor_name,
            "collaborator_pseudonym": "",
            "collaborator_roles": "",
            "seal_name": edition.seal.name,
            "editor_name": "",
            "translator_name": "",
            "adapter_name": overrides.get("adapter_name", contributor_name),
        }
    else:
        defaults = {
            "title": overrides.get("title", book_code),
            "subtitle": overrides.get("subtitle", ""),
            "author_name": overrides.get("author_name", ""),
            "publication_year": timezone.now().year,
            "imprint_name": "",
            "city_name": "Rio de Janeiro",
            "country_name": country_name,
            "collection_name": "",
            "collaborator_name": "",
            "collaborator_pseudonym": "",
            "collaborator_roles": "",
            "seal_name": "",
            "editor_name": "",
            "translator_name": "",
            "adapter_name": overrides.get("adapter_name", ""),
        }
    is_generic = edition is None

    template, created = BookEditionTemplate.objects.get_or_create(
        book_code=book_code,
        language=language,
        defaults=defaults,
    )
    updated_fields = []
    if created:
        template.apply_language_defaults_if_empty()
        template.save()
    else:
        language_overrides = BOOK_LANGUAGE_DEFAULTS.get(book_code, {})
        title_candidates = [item["title"] for item in language_overrides.values() if item.get("title")]
        subtitle_candidates = [
            item["subtitle"] for item in language_overrides.values() if item.get("subtitle")
        ]
        author_candidates = [
            item["author_name"] for item in language_overrides.values() if item.get("author_name")
        ]
        adapter_candidates = [
            item["adapter_name"] for item in language_overrides.values() if item.get("adapter_name")
        ]

        if overrides:
            if (
                force_defaults
                or not template.title
                or template.title == book_code
                or _auto_value_match(template.title, title_candidates)
            ):
                template.title = overrides.get("title", template.title)
                updated_fields.append("title")
            if force_defaults or not template.subtitle or _auto_value_match(template.subtitle, subtitle_candidates):
                template.subtitle = overrides.get("subtitle", template.subtitle)
                updated_fields.append("subtitle")
            if force_defaults or not template.author_name or _auto_value_match(template.author_name, author_candidates):
                template.author_name = overrides.get("author_name", template.author_name)
                updated_fields.append("author_name")
            if force_defaults or not template.adapter_name or _auto_value_match(template.adapter_name, adapter_candidates):
                template.adapter_name = overrides.get("adapter_name", template.adapter_name)
                updated_fields.append("adapter_name")
            if force_defaults or not template.about_edition_text:
                template.about_edition_text = overrides.get(
                    "about_edition_text", template.about_edition_text
                )
                updated_fields.append("about_edition_text")

        if force_defaults:
            defaults = LANGUAGE_DEFAULT_TEMPLATES.get(language)
            if defaults:
                template.frontispiece_text = defaults["frontispiece_text"]
                template.copyright_text = defaults["copyright_text"]
                updated_fields.extend(["frontispiece_text", "copyright_text"])
            template.country_name = country_name
            updated_fields.append("country_name")
        elif not template.country_name:
            template.country_name = country_name
            updated_fields.append("country_name")
        if force_defaults:
            default_updates = template.apply_language_defaults_if_empty()
            if default_updates:
                updated_fields.extend(default_updates)

        if updated_fields:
            template.save(update_fields=updated_fields)
    files_exist = _frontmatter_files_exist(book_code, language)
    warning = ""

    if request.method == "POST":
        form = FrontmatterTemplateForm(request.POST, instance=template)
        if form.is_valid():
            confirm_overwrite = request.POST.get("confirm_overwrite") == "1"
            save_block = (request.POST.get("save_block") or "all").strip()
            if files_exist and not confirm_overwrite:
                warning = "Substituir arquivos atuais do frontmatter?"
            else:
                instance = form.save(commit=False)
                block_fields_map = {
                    "frontmatter": ["frontispiece_text", "copyright_text", "about_edition_text"],
                    "preface": ["has_preface", "preface_text"],
                    "introduction": ["has_introduction", "introduction_text"],
                    "epilogue": ["has_epilogue", "epilogue_text"],
                    "all": [
                        "seal_name",
                        "title",
                        "subtitle",
                        "author_name",
                        "publication_year",
                        "imprint_name",
                        "city_name",
                        "country_name",
                        "editor_name",
                        "translator_name",
                        "adapter_name",
                        "frontispiece_text",
                        "copyright_text",
                        "about_edition_text",
                        "has_preface",
                        "preface_text",
                        "has_introduction",
                        "introduction_text",
                        "has_epilogue",
                        "epilogue_text",
                        "about_contributor_text",
                    ],
                }
                update_fields = block_fields_map.get(save_block, block_fields_map["all"])
                instance.save(apply_defaults=False, update_fields=update_fields)
                if edition:
                    _sync_template_to_edition(template, edition)
                    _write_frontmatter_files(edition)
                    _mark_editorial_changed(edition)
                return redirect("frontmatter_template_edit", book_code=book_code, language=language)
    else:
        form = FrontmatterTemplateForm(instance=template)
        warning = ""

    rendered_sections = {
        "frontispiece": template.frontispiece_rendered,
        "copyright": template.copyright_rendered,
        "about_edition": template.about_edition_rendered,
        "preface": template.preface_rendered if template.has_preface else "",
        "introduction": template.introduction_rendered if template.has_introduction else "",
        "epilogue": template.epilogue_rendered if template.has_epilogue else "",
        "about_contributor": template.about_contributor_rendered,
    }
    preview_sections = build_frontmatter_sections(language, rendered_sections)

    context = {
        "edition": edition,
        "is_generic": is_generic,
        "form": form,
        "source_format": template.text_source_mode or "txt",
        "frontmatter_preview": preview_sections.get("frontispiece", ""),
        "copyright_preview": preview_sections.get("copyright", ""),
        "about_edition_preview": preview_sections.get("about_edition", ""),
        "preface_preview": preview_sections.get("preface", ""),
        "introduction_preview": preview_sections.get("introduction", ""),
        "epilogue_preview": preview_sections.get("epilogue", ""),
        "about_contributor_preview": preview_sections.get("about_contributor", ""),
        "frontmatter_merged_preview": merge_frontmatter_sections(preview_sections),
        "optional_section_warnings": optional_section_warnings(template, language),
        "language_options": BookEditionTemplate.LANG_CHOICES,
        "book_code": book_code,
        "language": language,
        "frontmatter_files_exist": files_exist,
        "overwrite_warning": warning,
    }
    return render(request, "editorial/frontmatter_form.html", context)


def editorial_frontmatter_actions(request, edition_id: int):
    edition = get_object_or_404(EditorialEdition, pk=edition_id)

    if request.method != "POST":
        return redirect("edition_steps", edition_id=edition.id)

    action = request.POST.get("action")
    target_lang = request.POST.get("target_lang") or edition.language.code

    try:
        if target_lang == edition.language.code:
            target_edition = edition
        else:
            target_edition = EditorialEdition.objects.get(
                work__code=edition.work.code,
                language__code=target_lang,
            )
    except EditorialEdition.DoesNotExist:
        messages.error(request, f"Edicao nao encontrada: {edition.work.code} [{target_lang}]")
        return redirect("edition_steps", edition_id=edition.id)

    if action == "rebuild_frontmatter":
        kdp_mode.build_frontmatter_files(target_edition, Path("data") / "frontmatter")
        messages.success(
            request,
            f"Frontmatter regenerado para {target_edition.work.code} [{target_edition.language.code}]",
        )
    elif action == "build_frontmatter_and_merged":
        kdp_mode.build_frontmatter_files(target_edition, Path("data") / "frontmatter")
        merged_path = kdp_mode.build_merged_kdp_source(target_edition)
        messages.success(request, f"Frontmatter + BOOK.BUILD.MD regenerados: {merged_path}")
    else:
        messages.warning(request, f"Acao desconhecida: {action}")

    return redirect("edition_steps", edition_id=edition.id)


@require_POST
def toggle_stage_lock(request, edition_id: int):
    edition = get_object_or_404(EditorialEdition, pk=edition_id)
    target_lang = utils.normalize_lang(request.POST.get("target_lang") or edition.language.code)

    if target_lang != utils.normalize_lang(edition.language.code):
        edition = get_object_or_404(
            EditorialEdition,
            work__code=edition.work.code,
            language__code=target_lang,
        )

    lock_name = request.POST.get("lock_name") or ""
    value = request.POST.get("value")
    if lock_name not in ("lock_translate", "lock_refine", "lock_polish"):
        messages.error(request, f"Lock invalido: {lock_name}")
        return redirect(request.META.get("HTTP_REFERER", "/"))

    lock_value = value == "1"
    if lock_value:
        for other in ("lock_translate", "lock_refine", "lock_polish"):
            if other != lock_name:
                setattr(edition, other, False)
    setattr(edition, lock_name, lock_value)
    update_fields = [lock_name]
    if lock_value:
        update_fields.extend(
            [other for other in ("lock_translate", "lock_refine", "lock_polish") if other != lock_name]
        )
    edition.save(update_fields=update_fields)
    state = "ON" if lock_value else "OFF"
    messages.success(
        request,
        f"{lock_name}={state} para {edition.work.code} [{edition.language.code}]",
    )
    return redirect(request.META.get("HTTP_REFERER", "/"))


def edition_edit(request, edition_id: int):
    edition = get_object_or_404(EditorialEdition, pk=edition_id)
    apply_defaults = request.GET.get("apply_defaults") == "1" or request.POST.get("apply_defaults") == "1"

    if request.method == "POST":
        form = EditionForm(request.POST, instance=edition)
        if form.is_valid():
            form.save()
            if apply_defaults:
                edition.refresh_from_db()
                book_defaults = BOOK_LANGUAGE_DEFAULTS.get(edition.work.code, {}).get(
                    edition.language_code, {}
                )
                updates = {
                    "title": book_defaults.get("title", edition.title),
                    "subtitle": book_defaults.get("subtitle", edition.subtitle),
                    "author": book_defaults.get("author_name", edition.author),
                    "adapter": book_defaults.get("adapter_name", edition.adapter),
                    "country": country_for_language(edition.language_code, edition.country),
                }
                for key, value in updates.items():
                    setattr(edition, key, value)
                edition.save(update_fields=list(updates.keys()))
            return redirect("edition_edit", edition_id=edition.id)
    else:
        form = EditionForm(instance=edition)
        if apply_defaults:
            book_defaults = BOOK_LANGUAGE_DEFAULTS.get(edition.work.code, {}).get(
                edition.language_code, {}
            )
            updates = {
                "title": book_defaults.get("title", edition.title),
                "subtitle": book_defaults.get("subtitle", edition.subtitle),
                "author": book_defaults.get("author_name", edition.author),
                "adapter": book_defaults.get("adapter_name", edition.adapter),
                "country": country_for_language(edition.language_code, edition.country),
            }
            for key, value in updates.items():
                setattr(edition, key, value)
            edition.save(update_fields=list(updates.keys()))
            form = EditionForm(instance=edition)

    country_label = country_for_language(edition.language_code, edition.country)
    frontispiece_template = get_frontispiece_template_for_edition(edition)
    copyright_template = get_section_template_for_language("copyright", edition.language_code)
    about_template = get_section_template_for_language("about_edition", edition.language_code)

    about_context = {
        "edition": edition,
        "country_label": country_label,
        "about_edition_text": edition.about_edition_text,
    }
    context = {
        "edition": edition,
        "form": form,
        "frontispiece_preview": render_to_string(
            frontispiece_template,
            {"edition": edition, "country_label": country_label},
        ),
        "copyright_preview": render_to_string(
            copyright_template,
            {"edition": edition, "country_label": country_label},
        ),
        "about_edition_preview": render_to_string(
            about_template,
            about_context,
        ),
    }
    return render(request, "gaiden/edition_form.html", context)


def frontispiece_preview(request, edition_id: int):
    edition = get_object_or_404(EditorialEdition, pk=edition_id)
    template_name = get_frontispiece_template_for_edition(edition)
    country_label = country_for_language(edition.language_code, edition.country)
    frontispiece_md = render_to_string(
        template_name,
        {"edition": edition, "country_label": country_label},
    )
    return render(
        request,
        "gaiden/frontispiece_preview.html",
        {
            "edition": edition,
            "frontispiece_md": frontispiece_md,
        },
    )
