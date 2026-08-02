from __future__ import annotations

from datetime import date
from typing import Any

from django.utils.text import slugify
from django.utils.dateparse import parse_datetime

from editorial.models import Contributor, ContributorRole, Edition, EditionPipeline, EditionText, Language, PipelineStage, Seal, Work
from pipeline.models import BookEditionTemplate, TextSnapshot

from .contracts import EditorialEditionPackage, ValidatedEditorialPackage, pipeline_language


TEMPLATE_FIELDS = {
    "title",
    "subtitle",
    "author_name",
    "publication_year",
    "original_publication_date",
    "original_author_death_date",
    "work_kind",
    "imprint_name",
    "collection_name",
    "collaborator_name",
    "collaborator_pseudonym",
    "collaborator_roles",
    "seal_name",
    "editor_name",
    "translator_name",
    "adapter_name",
    "editorial_name",
    "edition_year",
    "edition_copyright_holder",
    "cover_filepath",
    "images_dir",
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
    "text_source_mode",
    "registration_status",
    "source_file_type",
    "source_original_name",
    "source_saved_path",
    "source_file_size",
    "source_uploaded_at",
    "source_file_sha256",
    "source_uploaded_by",
}


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _language(candidates: list[str]) -> Language:
    language = Language.objects.select_for_update().filter(code__in=[value for value in candidates if value], is_active=True).order_by("id").first()
    if language is None:
        raise ValueError(f"Idioma ativo não cadastrado: {candidates[0] if candidates else '—'}.")
    return language


def _edition_language(package: EditorialEditionPackage) -> Language:
    code = pipeline_language(package.language, package.locale)
    return _language([code, package.locale.lower(), package.locale.lower().replace("-", ""), package.language.lower()])


def _contributor(name: str, role: str) -> Contributor:
    normalized = " ".join(name.split())
    existing = Contributor.objects.select_for_update().filter(name__iexact=normalized, role=role).order_by("id").first()
    return existing or Contributor.objects.create(name=normalized, role=role)


def _seal(package: EditorialEditionPackage) -> Seal:
    name = str(package.metadata.get("seal_name") or package.metadata.get("imprint_name") or "Gaiden").strip()
    slug = slugify(name) or "gaiden"
    seal = Seal.objects.select_for_update().filter(slug=slug).first()
    if seal:
        return seal
    return Seal.objects.create(slug=slug, name=name)


def _work(validated: ValidatedEditorialPackage) -> tuple[Work, str]:
    package = validated.package
    original_language = _language([str(package.book["source_language"]).lower()])
    author = _contributor(str(package.book["author_name"]), ContributorRole.AUTHOR)
    work = Work.objects.select_for_update().filter(code=package.book_code).first()
    original_date = _parse_date(package.book.get("original_publication_date"))
    proposed = {
        "title": str(package.book["title"]),
        "original_language": original_language,
        "author": author,
        "publisher": str(package.book.get("publisher") or ""),
        "year": original_date.year if original_date else None,
        "is_public_domain": package.book.get("work_kind") == "PUBLIC_DOMAIN",
    }
    if work is None:
        return Work.objects.create(code=package.book_code, **proposed), "CREATE"
    if (
        work.title.strip().casefold() != str(package.book["title"]).strip().casefold()
        or work.author_id != author.id
        or work.original_language_id != original_language.id
    ):
        raise ValueError(f"Work {package.book_code} possui identidade incompatível.")
    changed = [name for name, value in proposed.items() if getattr(work, name) != value]
    for name in changed:
        setattr(work, name, proposed[name])
    if changed:
        work.save(update_fields=changed)
    return work, "UPDATE" if changed else "NO_OP"


def _template_values(validated: ValidatedEditorialPackage, package: EditorialEditionPackage) -> dict[str, Any]:
    source = validated.package.source
    values = {**package.metadata, **package.frontmatter}
    values.setdefault("author_name", validated.package.book["author_name"])
    values.setdefault("source_saved_path", source.path)
    values.setdefault("source_file_size", source.size)
    values.setdefault("source_file_sha256", source.sha256)
    values["original_publication_date"] = _parse_date(values.get("original_publication_date"))
    values["original_author_death_date"] = _parse_date(values.get("original_author_death_date"))
    uploaded_at = values.get("source_uploaded_at")
    if isinstance(uploaded_at, str):
        values["source_uploaded_at"] = parse_datetime(uploaded_at) if uploaded_at.strip() else None
    values["registration_status"] = BookEditionTemplate.STATUS_DRAFT
    return {name: values[name] for name in TEMPLATE_FIELDS if name in values and values[name] is not None}


def _project_edition(validated: ValidatedEditorialPackage, work: Work, package: EditorialEditionPackage) -> tuple[Edition, dict[str, Any]]:
    language = _edition_language(package)
    seal = _seal(package)
    metadata = package.metadata
    contributor_name = str(metadata.get("translator_name") or metadata.get("adapter_name") or metadata.get("collaborator_name") or "").strip()
    main_contributor = _contributor(contributor_name, ContributorRole.TRANSLATOR) if contributor_name else None
    edition, created = Edition.objects.select_for_update().get_or_create(
        work=work,
        language=language,
        seal=seal,
        defaults={"title": str(metadata.get("title") or "")},
    )
    values = {
        "main_contributor": main_contributor,
        "publisher": str(metadata.get("editorial_name") or metadata.get("imprint_name") or ""),
        "edition_year": metadata.get("edition_year") or metadata.get("publication_year"),
        "raw_source_path": validated.package.source.path,
        "title": str(metadata.get("title") or ""),
        "subtitle": str(metadata.get("subtitle") or ""),
        "author": str(metadata.get("author_name") or validated.package.book["author_name"]),
        "adapter": str(metadata.get("adapter_name") or ""),
        "translator": str(metadata.get("translator_name") or ""),
        "editor": str(metadata.get("editor_name") or ""),
        "about_edition_text": str(package.frontmatter.get("about_edition_text") or ""),
        "publication_year": metadata.get("publication_year") or 2026,
        "imprint_name": str(metadata.get("imprint_name") or "RinoBooks"),
        "seal_name": str(metadata.get("seal_name") or "MantaQuest"),
        "frontispiece_template": str(package.frontmatter.get("frontispiece_text") or ""),
        "copyright_template": str(package.frontmatter.get("copyright_text") or ""),
        "about_edition_template": str(package.frontmatter.get("about_edition_text") or ""),
        "about_contributor_template": str(package.frontmatter.get("about_contributor_text") or ""),
        "cover_filepath": str(metadata.get("cover_filepath") or ""),
        "language_code": language.code,
    }
    changed = [name for name, value in values.items() if getattr(edition, name) != value]
    for name in changed:
        setattr(edition, name, values[name])
    if changed:
        edition.save(update_fields=changed)

    pipeline, _ = EditionPipeline.objects.get_or_create(edition=edition, defaults={"current_stage": PipelineStage.RAW})
    template, template_created = BookEditionTemplate.objects.select_for_update().get_or_create(
        book_code=validated.package.book_code,
        language=pipeline_language(package.language, package.locale),
        defaults=_template_values(validated, package),
    )
    template_values = _template_values(validated, package)
    template_changed = [name for name, value in template_values.items() if getattr(template, name) != value]
    for name in template_changed:
        setattr(template, name, template_values[name])
    if template_created or template_changed:
        template.save(apply_defaults=False)
    return edition, {
        "edition": "CREATE" if created else ("UPDATE" if changed else "NO_OP"),
        "template": "CREATE" if template_created else ("UPDATE" if template_changed else "NO_OP"),
        "pipeline_stage": pipeline.current_stage,
    }


def project_catalog(validated: ValidatedEditorialPackage) -> tuple[dict[str, Any], dict[str, Edition]]:
    work, work_action = _work(validated)
    actions: dict[str, Any] = {"work": work_action, "editions": {}}
    editions: dict[str, Edition] = {}
    for package in validated.package.editions:
        edition, result = _project_edition(validated, work, package)
        editions[package.locale] = edition
        actions["editions"][package.locale] = result
    return actions, editions


def project_bodies_after_blocks(validated: ValidatedEditorialPackage, editions: dict[str, Edition], *, complete: bool) -> list[dict[str, Any]]:
    if not complete:
        return []
    results: list[dict[str, Any]] = []
    for package in validated.package.editions:
        if package.body is None or package.body.status == "ABSENT":
            continue
        edition = editions[package.locale]
        path = validated.artifact_paths[f"body:{package.locale}"]
        content = path.read_text(encoding="utf-8")
        texts, _ = EditionText.objects.select_for_update().get_or_create(edition=edition)
        if texts.normalized_text and texts.normalized_text != content:
            TextSnapshot.objects.create(
                edition=edition,
                language=pipeline_language(package.language, package.locale),
                stage="editorial_import_history",
                source_path=texts.normalized_path,
                content=texts.normalized_text,
            )
        action = "NO_OP" if texts.normalized_text == content else ("UPDATE" if texts.normalized_text else "CREATE")
        if action != "NO_OP":
            texts.normalized_text = content
            texts.normalized_path = package.body.path
            texts.save(update_fields=["normalized_text", "normalized_path", "updated_at"])
        results.append({"locale": package.locale, "action": action, "status": "EDITORIAL_REVIEW", "published": False})
    return results
