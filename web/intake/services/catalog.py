"""Read-only catalog projections over existing editorial and Intake records."""
from collections import defaultdict
from pathlib import Path

from editorial.models import Edition, Work
from pipeline.models import IntakeItem


REFERENCE_KINDS = {
    "reference", "article", "paper", "artigo", "referencia", "chapter",
    "excerpt", "notes", "bibliography", "project_auxiliary",
}


def is_reference(item):
    metadata = item.metadata or {}
    return item.extension.casefold() == ".pdf" or any(
        str(metadata.get(key, "")).casefold() in REFERENCE_KINDS
        for key in ("kind", "role", "document_type")
    )


def imported_titles():
    items = list(IntakeItem.objects.select_related("batch").exclude(canonical_path=""))
    items_by_code = defaultdict(list)
    reference_codes = set()
    for item in items:
        if is_reference(item):
            reference_codes.add(item.book_code)
        else:
            items_by_code[item.book_code].append(item)
    editions_by_code = defaultdict(list)
    nonreference_editions = set()
    for edition in Edition.objects.select_related("work", "language").order_by("id"):
        editions_by_code[edition.work.code].append(edition)
        if Path(edition.raw_source_path).suffix.casefold() == ".pdf":
            reference_codes.add(edition.work.code)
        elif edition.raw_source_path:
            nonreference_editions.add(edition.work.code)
    codes = set(items_by_code) | set(editions_by_code)
    rows = []
    for work in Work.objects.select_related("author").filter(code__in=codes).order_by("title", "code"):
        if (
            work.code in reference_codes
            and work.code not in items_by_code
            and work.code not in nonreference_editions
        ):
            continue
        provenance = work.source_provenance or {}
        if str(provenance.get("document_type", "")).casefold() in REFERENCE_KINDS:
            continue
        rows.append({"work": work, "editions": editions_by_code[work.code], "items": items_by_code[work.code]})
    return rows
