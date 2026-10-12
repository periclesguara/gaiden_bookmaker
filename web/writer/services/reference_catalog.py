"""Read-only catalog of canonical Intake works and shared vector availability."""

from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q

from editorial.models import Contributor, EditionBuild, Work
from ..models import ProjectReference, SourceVectorIndex
from .corpus import index_ready, reference_index


REFERENCE_ROLES = ("VOICE", "CANON", "CONTINUITY", "MYSTERY_STRUCTURE", "HISTORICAL", "STYLE", "TECHNICAL", "OTHER")


def author_catalog(query="", page=1):
    authors = Contributor.objects.filter(works_as_author__isnull=False)
    if query:
        authors = authors.filter(name__icontains=query)
    authors = authors.annotate(work_count=Count("works_as_author")).order_by("name", "pk")
    result = Paginator(authors, 30).get_page(page)
    return {
        "results": [{"id": author.pk, "name": author.name, "work_count": author.work_count} for author in result],
        "next_page": result.next_page_number() if result.has_next() else None,
    }


def work_queryset():
    builds = EditionBuild.objects.select_related("writer_vector_index").order_by("-build_version", "-created_at", "-pk")
    return Work.objects.select_related("author", "original_language").prefetch_related(
        Prefetch("editions__build_history", queryset=builds),
    )


def serialize_work(work):
    builds = [build for edition in work.editions.all() for build in edition.build_history.all()]
    ready = []
    for build in builds:
        try:
            index = build.writer_vector_index
        except SourceVectorIndex.DoesNotExist:
            continue
        if (index.status == SourceVectorIndex.Status.READY and build.official_body_sha256
                and index.source_hash == build.official_body_sha256
                and build._safe_existing_path(index.index_path) is not None):
            ready.append(build)
    source = max(ready or builds, key=lambda build: (build.is_final, build.build_version, build.created_at, build.pk), default=None)
    if any(build.status == EditionBuild.STATUS_DONE and build.is_final and build.validation_passed for build in builds):
        state = "EDITED"
    elif any(build.status in {EditionBuild.STATUS_IN_PROGRESS, EditionBuild.STATUS_VALIDATING, EditionBuild.STATUS_READY_FOR_APPROVAL} for build in builds):
        state = "IN PRODUCTION"
    else:
        state = "RAW"
    shared = reference_index(ProjectReference(intake_work=work))
    return {
        "work_id": work.pk, "code": work.code, "source_id": source.pk if source else None,
        "title": work.title, "author_id": work.author_id, "author": work.author.name,
        "language": work.original_language.code, "state": state, "vectorized": bool(shared and index_ready(shared)),
    }


def works_for_author(author_id=None, query="", page=1):
    works = work_queryset()
    if author_id:
        works = works.filter(author_id=author_id)
    elif not query:
        return {"results": [], "next_page": None}
    if query:
        works = works.filter(Q(title__icontains=query) | Q(author__name__icontains=query))
    result = Paginator(works.order_by("title", "pk"), 20).get_page(page)
    return {
        "results": [serialize_work(work) for work in result],
        "next_page": result.next_page_number() if result.has_next() else None,
    }
