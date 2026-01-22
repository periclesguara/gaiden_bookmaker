from __future__ import annotations

from django.contrib import messages
from django.shortcuts import render, redirect

from editorial.models import Edition, PipelineArtifact
from editorial.services.artifact_index import reindex_artifacts_for_work
from pipeline.services import utils


def artifacts_table(request, work_code: str, lang: str):
    normalized_lang = utils.normalize_lang(lang)
    artifacts = (
        PipelineArtifact.objects.filter(work_code=work_code, language_code=normalized_lang)
        .order_by("stage", "-mtime_iso")
    )

    edition = None
    try:
        edition = Edition.objects.get(work__code=work_code, language__code=normalized_lang)
    except Edition.DoesNotExist:
        edition = None

    return render(
        request,
        "editorial/artifacts_table.html",
        {
            "work_code": work_code,
            "lang": normalized_lang,
            "edition": edition,
            "artifacts": artifacts,
        },
    )


def artifacts_reindex(request, work_code: str):
    reindex_artifacts_for_work(work_code)
    messages.success(request, f"Reindex OK: {work_code}")
    return redirect("artifacts_table", work_code=work_code, lang="en")
