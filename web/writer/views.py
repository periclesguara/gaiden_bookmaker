from __future__ import annotations

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from writer.forms import (
    ChapterForm,
    ProjectSourcesForm,
    StoryProjectForm,
    SupportingCastUpdateForm,
)
from writer.models import Chapter, SourceDocument, StoryProject
from writer.services.generation import generate_chapter
from writer.services.normalization import normalize_document
from writer.services.projects import synchronize_chapters
from writer.services.sources import discover_source_documents
from writer.services.supporting_characters import (
    generate_supporting_characters_bible,
    update_supporting_characters_bible,
)
from writer.services.vectorization import vectorize_project


@staff_member_required
@require_GET
def home(request: HttpRequest) -> HttpResponse:
    projects = StoryProject.objects.prefetch_related("chapters")
    return render(request, "writer/home.html", {
        "projects": projects,
        "source_count": SourceDocument.objects.count(),
    })


@staff_member_required
@require_GET
def sources(request: HttpRequest) -> HttpResponse:
    return render(request, "writer/sources.html", {"documents": SourceDocument.objects.all()})


@staff_member_required
@require_POST
def scan_sources(request: HttpRequest) -> HttpResponse:
    try:
        created = discover_source_documents()
        messages.success(request, f"{created} novo(s) arquivo(s) localizado(s).")
    except Exception as exc:
        messages.error(request, f"Falha ao localizar arquivos: {exc}")
    return redirect("writer:sources")


@staff_member_required
@require_POST
def normalize_sources(request: HttpRequest) -> HttpResponse:
    raw_ids = request.POST.getlist("documents")
    if not raw_ids:
        messages.error(request, "Selecione ao menos um arquivo.")
        return redirect("writer:sources")
    documents = list(SourceDocument.objects.filter(id__in=raw_ids))
    for document in documents:
        try:
            normalize_document(document)
        except Exception as exc:
            document.status = SourceDocument.Status.FAILED
            document.error_message = str(exc)[:2000]
            document.save(update_fields=("status", "error_message"))
            messages.error(request, f"{document.filename}: {exc}")
    successful = sum(document.status == SourceDocument.Status.NORMALIZED for document in documents)
    if successful:
        messages.success(request, f"{successful} arquivo(s) normalizado(s).")
    return redirect("writer:sources")


@staff_member_required
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


@staff_member_required
@require_GET
def project_detail(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(
        StoryProject.objects.prefetch_related("chapters__sessions", "sources"), pk=project_id
    )
    return render(request, "writer/project_detail.html", {
        "project": project,
        "source_form": ProjectSourcesForm(project=project),
        "supporting_cast_update_form": SupportingCastUpdateForm(),
        "supporting_cast_revisions": project.supporting_cast_revisions.select_related(
            "created_by"
        )[:10],
    })


@staff_member_required
@require_POST
def generate_supporting_characters(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    if project.supporting_characters_bible.strip() and request.POST.get("confirm") != "yes":
        messages.error(
            request,
            "A bíblia dos coadjuvantes já existe. Confirme explicitamente para substituí-la.",
        )
        return redirect("writer:project_detail", project_id=project.id)
    try:
        generate_supporting_characters_bible(project, created_by=request.user)
        messages.success(
            request,
            "Bíblia estruturada dos coadjuvantes criada pela IA e salva no projeto.",
        )
    except Exception as exc:
        messages.error(request, f"Falha ao gerar coadjuvantes: {exc}")
    return redirect("writer:project_detail", project_id=project.id)


@staff_member_required
@require_POST
def update_supporting_characters(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    form = SupportingCastUpdateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Descreva a atualização ou o gap de continuidade.")
        return redirect("writer:project_detail", project_id=project.id)
    try:
        revision = update_supporting_characters_bible(
            project,
            form.cleaned_data["instruction"],
            created_by=request.user,
        )
        messages.success(
            request,
            f"Bíblia dos coadjuvantes atualizada para a revisão v{revision.version}.",
        )
    except Exception as exc:
        messages.error(request, f"Falha ao atualizar coadjuvantes: {exc}")
    return redirect("writer:project_detail", project_id=project.id)


@staff_member_required
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
        messages.success(request, "Fontes do projeto atualizadas.")
    else:
        messages.error(request, "Seleção de fontes inválida.")
    return redirect("writer:project_detail", project_id=project.id)


@staff_member_required
@require_POST
def vectorize(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    try:
        vectorize_project(project)
        messages.success(request, "Fontes vetorizadas e índice RAG atualizado.")
    except Exception as exc:
        messages.error(request, f"Falha na vetorização: {exc}")
    return redirect("writer:project_detail", project_id=project.id)


@staff_member_required
@require_GET
def chapter_detail(request: HttpRequest, chapter_id: int) -> HttpResponse:
    chapter = get_object_or_404(
        Chapter.objects.select_related("project").prefetch_related("sessions"), pk=chapter_id
    )
    return render(request, "writer/chapter_detail.html", {"chapter": chapter})


@staff_member_required
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


@staff_member_required
@require_POST
def generate(request: HttpRequest, chapter_id: int) -> HttpResponse:
    chapter = get_object_or_404(Chapter.objects.select_related("project"), pk=chapter_id)
    try:
        generate_chapter(chapter)
        messages.success(request, "Todas as sessões configuradas foram geradas.")
    except Exception as exc:
        messages.error(request, f"Falha na geração: {exc}")
    return redirect("writer:chapter_detail", chapter_id=chapter.id)


@staff_member_required
@require_POST
def finalize(request: HttpRequest, chapter_id: int) -> HttpResponse:
    chapter = get_object_or_404(Chapter, pk=chapter_id)
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Confirmação editorial obrigatória.")
        return redirect("writer:chapter_detail", chapter_id=chapter.id)
    try:
        chapter.finalize()
        messages.success(request, "Capítulo finalizado após confirmação editorial.")
    except Exception as exc:
        messages.error(request, f"Não foi possível finalizar: {exc}")
    return redirect("writer:chapter_detail", chapter_id=chapter.id)
