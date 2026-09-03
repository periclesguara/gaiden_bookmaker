from __future__ import annotations

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .forms import ChapterForm, ProjectSourcesForm, StoryProjectForm
from .models import Chapter, SourceDocument, StoryProject
from .services.dashboard import build_project_dashboard
from .services.generation import generate_chapter
from .services.normalization import normalize_document
from .services.projects import synchronize_chapters
from .services.sources import discover_source_documents
from .services.vectorization import vectorize_project


@require_GET
def home(request: HttpRequest) -> HttpResponse:
    projects = StoryProject.objects.prefetch_related("chapters")
    return render(request, "writer/home.html", {
        "projects": projects,
        "source_count": SourceDocument.objects.count(),
    })


@require_GET
def sources(request: HttpRequest) -> HttpResponse:
    documents = list(SourceDocument.objects.all())
    normalized = sum(
        document.status in {SourceDocument.Status.NORMALIZED, SourceDocument.Status.VECTORIZED}
        and bool(document.normalized_path and document.normalized_sha256)
        for document in documents
    )
    return render(request, "writer/sources.html", {
        "documents": documents,
        "normalized_count": normalized,
        "normalization_complete": bool(documents) and normalized == len(documents),
    })


@require_POST
def scan_sources(request: HttpRequest) -> HttpResponse:
    try:
        created = discover_source_documents()
        if created:
            messages.success(request, f"Localização concluída: {created} novo(s) arquivo(s).")
        else:
            messages.success(request, "Localização concluída: nenhum arquivo novo encontrado.")
    except Exception as exc:
        messages.error(request, f"Falha ao localizar arquivos: {exc}")
    return redirect("writer:sources")


@require_POST
def normalize_sources(request: HttpRequest) -> HttpResponse:
    confirmed = request.POST.get("confirm") == "yes"
    remake = request.POST.get("remake") == "yes"
    if not confirmed and not remake:
        messages.warning(request, "Abra a confirmação antes de iniciar a normalização.")
        return redirect("writer:sources")
    raw_ids = request.POST.getlist("documents")
    if remake and not raw_ids:
        raw_ids = [str(value) for value in SourceDocument.objects.values_list("id", flat=True)]
    if not raw_ids:
        messages.warning(request, "Normalização pendente: selecione ao menos um arquivo.")
        return redirect("writer:sources")
    documents = list(SourceDocument.objects.filter(id__in=raw_ids))
    already_completed = [
        document for document in documents
        if document.status in {SourceDocument.Status.NORMALIZED, SourceDocument.Status.VECTORIZED}
        and document.normalized_path and document.normalized_sha256
    ]
    pending = documents if remake else [
        document for document in documents if document not in already_completed
    ]
    if remake and pending:
        affected_project_ids = StoryProject.objects.filter(
            sources__in=pending
        ).values_list("id", flat=True)
        StoryProject.objects.filter(id__in=affected_project_ids).update(
            vector_index_path="", updated_at=timezone.now()
        )
    for document in pending:
        try:
            normalize_document(document)
        except Exception as exc:
            document.status = SourceDocument.Status.FAILED
            document.error_message = str(exc)[:2000]
            document.save(update_fields=("status", "error_message"))
            messages.error(request, f"{document.filename}: {exc}")
    successful = sum(
        document.status in {SourceDocument.Status.NORMALIZED, SourceDocument.Status.VECTORIZED}
        for document in documents
    )
    if successful == len(documents):
        if remake:
            messages.success(
                request,
                f"Renormalização concluída: {successful} arquivo(s). "
                "Os índices relacionados foram invalidados e devem ser refeitos.",
            )
        else:
            messages.success(
                request,
                f"Normalização concluída: {successful} arquivo(s); "
                f"{len(already_completed)} já estava(m) pronto(s).",
            )
    elif successful:
        messages.warning(request, f"Normalização parcial: {successful} de {len(documents)} arquivos.")
    return redirect("writer:sources")


@require_http_methods(["GET", "POST"])
def project_edit(request: HttpRequest, project_id: int | None = None) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id) if project_id else StoryProject()
    form = StoryProjectForm(request.POST or None, instance=project)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            project = form.save()
            synchronize_chapters(project)
        messages.success(request, "Projeto e tabela de capítulos salvos.")
        return redirect("writer:project_detail", project_id=project.id)
    return render(request, "writer/project_form.html", {"form": form, "project": project})


@require_GET
def project_detail(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(
        StoryProject.objects.prefetch_related("chapters__sessions", "sources"), pk=project_id
    )
    dashboard = build_project_dashboard(project)
    return render(request, "writer/project_detail.html", {
        "project": project,
        "source_form": ProjectSourcesForm(project=project),
        "dashboard": dashboard,
        "workflow_stages": dashboard["stages"],
    })


@require_POST
def stage_status(request: HttpRequest, project_id: int, stage_number: int) -> HttpResponse:
    project = get_object_or_404(
        StoryProject.objects.prefetch_related("chapters__sessions", "sources"), pk=project_id
    )
    dashboard = build_project_dashboard(project)
    stage = next(
        (item for item in dashboard["stages"] if item["number"] == stage_number), None
    )
    if stage is None:
        raise Http404("unknown writer stage")
    message_label = {
        1: "Normalização",
        2: "Vetorização",
        3: "Bíblias",
        4: "Roteiro",
        5: "Parâmetros",
        6: "Geração",
    }[stage_number]
    if stage["complete"]:
        messages.success(request, f"{message_label} concluída: {stage['detail']}.")
    else:
        messages.warning(request, f"{message_label} pendente: {stage['detail']}.")
    return redirect("writer:project_detail", project_id=project.id)


@require_POST
def project_sources(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    form = ProjectSourcesForm(request.POST, project=project)
    if form.is_valid():
        selected = form.cleaned_data["sources"]
        old_ids = set(project.sources.values_list("id", flat=True))
        new_ids = set(selected.values_list("id", flat=True))
        project.sources.set(selected)
        if old_ids != new_ids:
            project.vector_index_path = ""
            project.save(update_fields=("vector_index_path", "updated_at"))
            messages.success(request, "Seleção atualizada; a vetorização precisa ser refeita.")
        else:
            messages.success(request, f"Seleção concluída: {len(new_ids)} fontes mantidas.")
    else:
        messages.error(request, "Seleção de fontes inválida.")
    return redirect("writer:project_detail", project_id=project.id)


@require_POST
def vectorize(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(
        StoryProject.objects.prefetch_related("chapters__sessions", "sources"), pk=project_id
    )
    dashboard = build_project_dashboard(project)
    confirmed = request.POST.get("confirm") == "yes"
    remake = request.POST.get("remake") == "yes"
    if not confirmed and not remake:
        messages.warning(request, "Abra a confirmação antes de iniciar a vetorização.")
        return redirect("writer:project_detail", project_id=project.id)
    if dashboard["sources"]["all_vectorized"] and not remake:
        messages.success(
            request,
            "Vetorização concluída: "
            f"{dashboard['sources']['vectorized']} fontes e "
            f"{dashboard['index']['chunk_count']} trechos no índice.",
        )
        return redirect("writer:project_detail", project_id=project.id)
    if dashboard["vectorize_blockers"]:
        messages.warning(
            request,
            "Vetorização pendente: " + "; ".join(dashboard["vectorize_blockers"]) + ".",
        )
        return redirect("writer:project_detail", project_id=project.id)
    try:
        vectorize_project(project)
        if remake:
            messages.success(request, "Revetorização concluída e índice RAG reconstruído.")
        else:
            messages.success(request, "Vetorização concluída e índice RAG criado.")
    except Exception as exc:
        messages.error(request, f"Falha na vetorização: {exc}")
    return redirect("writer:project_detail", project_id=project.id)


@require_GET
def chapter_detail(request: HttpRequest, chapter_id: int) -> HttpResponse:
    chapter = get_object_or_404(
        Chapter.objects.select_related("project").prefetch_related("sessions"), pk=chapter_id
    )
    return render(request, "writer/chapter_detail.html", {"chapter": chapter})


@require_http_methods(["GET", "POST"])
def chapter_edit(request: HttpRequest, chapter_id: int) -> HttpResponse:
    chapter = get_object_or_404(Chapter.objects.select_related("project"), pk=chapter_id)
    if chapter.status == Chapter.Status.FINAL:
        raise Http404("finalized chapters are immutable")
    form = ChapterForm(request.POST or None, instance=chapter)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Parâmetros do capítulo salvos.")
        return redirect("writer:project_detail", project_id=chapter.project_id)
    return render(request, "writer/chapter_form.html", {"chapter": chapter, "form": form})


@require_POST
def generate(request: HttpRequest, chapter_id: int) -> HttpResponse:
    chapter = get_object_or_404(Chapter.objects.select_related("project"), pk=chapter_id)
    if request.POST.get("confirm") != "yes":
        messages.warning(request, "Confirme a geração na caixa antes de iniciar o modelo.")
        return redirect("writer:project_detail", project_id=chapter.project_id)
    dashboard = build_project_dashboard(chapter.project)
    row = next(
        item for item in dashboard["chapter_rows"] if item["chapter"].pk == chapter.pk
    )
    if not row["generation_ready"]:
        if chapter.status == Chapter.Status.FINAL:
            messages.success(request, "Finalização concluída: este capítulo já está finalizado.")
        elif chapter.status == Chapter.Status.GENERATION_COMPLETE:
            messages.success(
                request, "Geração concluída: revise as sessões e finalize o capítulo."
            )
        else:
            messages.warning(
                request, "Geração pendente: " + "; ".join(row["generation_blockers"]) + "."
            )
        return redirect("writer:project_detail", project_id=chapter.project_id)
    try:
        generate_chapter(chapter)
        messages.success(request, "Todas as sessões configuradas foram geradas.")
    except Exception as exc:
        messages.error(request, f"Falha na geração: {exc}")
    return redirect("writer:chapter_detail", chapter_id=chapter.id)


@require_POST
def finalize(request: HttpRequest, chapter_id: int) -> HttpResponse:
    chapter = get_object_or_404(Chapter, pk=chapter_id)
    if chapter.status == Chapter.Status.FINAL:
        messages.success(request, "Finalização concluída: este capítulo já está finalizado.")
        return redirect("writer:chapter_detail", chapter_id=chapter.id)
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Confirmação editorial obrigatória.")
        return redirect("writer:chapter_detail", chapter_id=chapter.id)
    try:
        chapter.finalize()
        messages.success(request, "Capítulo finalizado após confirmação editorial.")
    except Exception as exc:
        messages.error(request, f"Não foi possível finalizar: {exc}")
    return redirect("writer:chapter_detail", chapter_id=chapter.id)
