from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from django.db.models import QuerySet
from django.utils.text import slugify

from editorial.models import Edition, Language, Seal, Work
from pipeline.models import BookEditionTemplate, IncrementalEdition
from pipeline.services.incremental_import import CONFIRMED_STATUSES, PreviewResult, preview_manifest

from .contracts import EditorialEditionPackage, ValidatedEditorialPackage, pipeline_language
from .validation import EditorialPackageValidationError, load_and_validate_package


def _language_candidates(edition: EditorialEditionPackage) -> tuple[str, ...]:
    canonical = pipeline_language(edition.language, edition.locale)
    values = [canonical, edition.locale.lower(), edition.locale.lower().replace("-", ""), edition.language.lower()]
    return tuple(dict.fromkeys(value for value in values if value))


def _find_language(edition: EditorialEditionPackage) -> Language | None:
    candidates = _language_candidates(edition)
    return Language.objects.filter(code__in=candidates, is_active=True).order_by("id").first()


def _find_seal(edition: EditorialEditionPackage) -> Seal | None:
    name = str(edition.metadata.get("seal_name") or edition.metadata.get("imprint_name") or "").strip()
    if not name:
        return None
    return Seal.objects.filter(slug=slugify(name)).first() or Seal.objects.filter(name__iexact=name).first()


def _changed_fields(instance: Any, proposed: dict[str, Any]) -> list[str]:
    return sorted(name for name, value in proposed.items() if getattr(instance, name) != value)


def _year(value: Any) -> int | None:
    try:
        return date.fromisoformat(str(value)).year if value else None
    except ValueError:
        return None


def _edition_operations(validated: ValidatedEditorialPackage) -> tuple[list[dict[str, Any]], list[str]]:
    package = validated.package
    operations: list[dict[str, Any]] = []
    conflicts: list[str] = []
    work = Work.objects.filter(code=package.book_code).select_related("author", "original_language").first()
    if work is None:
        operations.append({"entity": "Work", "identity": package.book_code, "action": "CREATE", "fields": ["title", "author", "original_language"]})
    else:
        mismatches = []
        if work.title.strip().casefold() != str(package.book["title"]).strip().casefold():
            mismatches.append("title")
        if work.author.name.strip().casefold() != str(package.book["author_name"]).strip().casefold():
            mismatches.append("author")
        if work.original_language.code.strip().casefold() != str(package.book["source_language"]).strip().casefold():
            mismatches.append("original_language")
        if mismatches:
            conflicts.append(f"Work {package.book_code} possui identidade incompatível: {', '.join(mismatches)}.")
            operations.append({"entity": "Work", "identity": package.book_code, "action": "CONFLICT", "fields": mismatches})
        else:
            proposed = {
                "publisher": str(package.book.get("publisher") or ""),
                "year": _year(package.book.get("original_publication_date")),
                "is_public_domain": package.book.get("work_kind") == "PUBLIC_DOMAIN",
            }
            changed = _changed_fields(work, proposed)
            operations.append({"entity": "Work", "identity": package.book_code, "action": "UPDATE" if changed else "NO_OP", "fields": changed})

    for edition_package in package.editions:
        language = _find_language(edition_package)
        if language is None:
            conflict = f"Idioma ativo não cadastrado: {edition_package.locale or edition_package.language}."
            conflicts.append(conflict)
            operations.append({"entity": "Edition", "identity": edition_package.locale, "action": "CONFLICT", "fields": ["language"]})
            continue
        seal = _find_seal(edition_package)
        seal_name = str(edition_package.metadata.get("seal_name") or edition_package.metadata.get("imprint_name") or "").strip()
        work_queryset: QuerySet[Work] = Work.objects.filter(code=package.book_code)
        existing = None
        if work_queryset.exists() and seal:
            existing = Edition.objects.filter(work=work_queryset.first(), language=language, seal=seal).first()
        if existing is None:
            operations.append({"entity": "Edition", "identity": f"{package.book_code}:{edition_package.locale}:{seal_name}", "action": "CREATE", "fields": ["metadata", "frontmatter"]})
        else:
            proposed = {
                "title": str(edition_package.metadata.get("title") or ""),
                "subtitle": str(edition_package.metadata.get("subtitle") or ""),
                "author": str(edition_package.metadata.get("author_name") or package.book["author_name"]),
                "adapter": str(edition_package.metadata.get("adapter_name") or ""),
                "translator": str(edition_package.metadata.get("translator_name") or ""),
                "editor": str(edition_package.metadata.get("editor_name") or ""),
            }
            changed = _changed_fields(existing, proposed)
            operations.append({"entity": "Edition", "identity": f"{package.book_code}:{edition_package.locale}:{seal_name}", "action": "UPDATE" if changed else "NO_OP", "fields": changed})

        pipeline_lang = pipeline_language(edition_package.language, edition_package.locale)
        template = BookEditionTemplate.objects.filter(book_code=package.book_code, language=pipeline_lang).first()
        if template is None:
            action, fields = "CREATE", ["metadata", "frontmatter"]
        else:
            proposed = {
                "title": str(edition_package.metadata.get("title") or ""),
                "frontispiece_text": str(edition_package.frontmatter.get("frontispiece_text") or ""),
                "copyright_text": str(edition_package.frontmatter.get("copyright_text") or ""),
                "about_edition_text": str(edition_package.frontmatter.get("about_edition_text") or ""),
            }
            fields = _changed_fields(template, proposed)
            action = "UPDATE" if fields else "NO_OP"
        operations.append({"entity": "BookEditionTemplate", "identity": f"{package.book_code}:{pipeline_lang}", "action": action, "fields": fields})
    return operations, conflicts


def _validate_package_manifest_identity(validated: ValidatedEditorialPackage, incremental: PreviewResult) -> None:
    package = validated.package
    manifest = incremental.manifest
    errors: list[str] = []
    if manifest["book_code"] != package.book_code:
        errors.append("O book_code do manifesto não corresponde ao pacote editorial.")
    if manifest["work_id"] != package.book_code:
        errors.append("O work_id do manifesto não corresponde à obra do pacote editorial.")
    if not manifest["edition_id"].startswith(f"{package.book_code}:{manifest['locale']}:"):
        errors.append("O edition_id do manifesto não corresponde a book_code e locale declarados.")
    if not any(edition.locale == manifest["locale"] for edition in package.editions):
        errors.append("O locale do manifesto não existe nas edições do pacote.")
    declared = package.incremental
    if declared:
        if declared.get("expected_block_count") not in (None, manifest["expected_block_count"]):
            errors.append("expected_block_count diverge entre pacote e manifesto.")
        if declared.get("manifest_sha256") and declared["manifest_sha256"] != incremental.manifest_sha256:
            errors.append("manifest_sha256 diverge entre pacote e manifesto.")
    if errors:
        raise EditorialPackageValidationError(errors)


def preview_editorial_import(
    package_path: str | Path,
    manifest_path: str | Path,
    *,
    artifact_root: str | Path | None = None,
    blocks_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Build a complete import plan without writing database or canonical files."""
    validated = load_and_validate_package(package_path, artifact_root=artifact_root)
    incremental = preview_manifest(manifest_path, blocks_directory=blocks_directory)
    _validate_package_manifest_identity(validated, incremental)
    operations, conflicts = _edition_operations(validated)
    conflicts.extend(row["detail"] for row in incremental.rows if row["action"] == "CONFLICT")
    block_counts = {"CREATE": 0, "UPDATE": 0, "NOOP": 0, "CONFLICT": 0}
    for row in incremental.rows:
        block_counts[row["action"]] += 1
    existing = IncrementalEdition.objects.filter(edition_id=incremental.manifest["edition_id"]).first()
    present_sequences = set()
    if existing:
        present_sequences.update(
            existing.blocks.filter(is_current=True, status__in=CONFIRMED_STATUSES).values_list("sequence", flat=True)
        )
    present_sequences.update(
        row["sequence"] for row in incremental.rows if not row.get("error") and row["action"] != "CONFLICT"
    )
    missing = [sequence for sequence in range(1, incremental.manifest["expected_block_count"] + 1) if sequence not in present_sequences]
    proposed_contiguous = 0
    while proposed_contiguous + 1 in present_sequences:
        proposed_contiguous += 1
    proposed_next = None if proposed_contiguous == incremental.manifest["expected_block_count"] else proposed_contiguous + 1
    return {
        "package": validated,
        "package_sha256": validated.package_sha256,
        "manifest_sha256": incremental.manifest_sha256,
        "book_code": validated.package.book_code,
        "status": validated.package.status,
        "operations": operations,
        "conflicts": conflicts,
        "warnings": list(validated.warnings),
        "incremental": incremental,
        "block_counts": block_counts,
        "missing_sequences": missing,
        "proposed_last_contiguous_sequence": proposed_contiguous,
        "proposed_next_sequence": proposed_next,
        "can_confirm": incremental.can_import and not conflicts,
    }
