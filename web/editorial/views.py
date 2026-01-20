from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone

from editorial.models import Edition as EditorialEdition
from gaiden_portal.forms import EditionForm
from gaiden_portal.utils import (
    country_for_language,
    get_frontispiece_template_for_edition,
    get_section_template_for_language,
)
from pipeline.models import BookEditionTemplate, LANGUAGE_DEFAULT_TEMPLATES
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
        default_updates = template.apply_language_defaults_if_empty()
        if default_updates:
            updated_fields.extend(default_updates)

        if updated_fields:
            template.save(update_fields=updated_fields)

    if request.method == "POST":
        form = FrontmatterTemplateForm(request.POST, instance=template)
        if form.is_valid():
            form.save()
            return redirect("frontmatter_template_edit", book_code=book_code, language=language)
    else:
        form = FrontmatterTemplateForm(instance=template)

    context = {
        "edition": edition,
        "is_generic": is_generic,
        "form": form,
        "frontmatter_preview": template.frontispiece_rendered,
        "copyright_preview": template.copyright_rendered,
        "language_options": BookEditionTemplate.LANG_CHOICES,
        "book_code": book_code,
        "language": language,
    }
    return render(request, "editorial/frontmatter_form.html", context)


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
            {"edition": edition, "country_label": country_label},
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
