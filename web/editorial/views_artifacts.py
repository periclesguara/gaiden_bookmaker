from __future__ import annotations

from pathlib import Path

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, render, redirect

from editorial.models import Edition, PipelineArtifact
from editorial.services.artifact_index import reindex_artifacts_for_work
from gaiden.infrastructure import storage
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


def artifact_preview(request, artifact_id: int):
    artifact = get_object_or_404(PipelineArtifact, id=artifact_id)
    root = storage.repo_root().resolve()
    path = (root / artifact.relpath).resolve()
    if root not in path.parents and path != root:
        raise Http404("Artifact path outside project root.")
    if not path.exists() or not path.is_file():
        raise Http404("Artifact file not found.")
    if path.suffix.lower() not in {".txt", ".md", ".json", ".jsonl", ".html", ".htm"}:
        raise Http404("Artifact preview is available only for text files.")

    content = path.read_text(encoding="utf-8", errors="replace")
    return render(
        request,
        "pipeline/preview_md.html",
        {
            "book_code": artifact.work_code,
            "language": artifact.language_code,
            "md_path": str(path),
            "content": content,
        },
    )
