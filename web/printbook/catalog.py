"""Read-only catalog of final, integrity-checked editorial EPUBs."""
from editorial.models import EditionBuild


def done_sources():
    return EditionBuild.objects.filter(
        status=EditionBuild.STATUS_DONE, is_final=True, validation_passed=True,
    ).select_related("edition__work__author", "edition__language")


def eligible(source):
    return source.status == EditionBuild.STATUS_DONE and source.qualifies_as_done


def catalog():
    return [source for source in done_sources().order_by("edition_id", "-build_version", "-pk") if eligible(source)]


def latest_by_edition(edition_ids):
    result = {}
    for source in done_sources().filter(edition_id__in=edition_ids).order_by("-build_version", "-pk"):
        if source.edition_id not in result and eligible(source):
            result[source.edition_id] = source
    return result
