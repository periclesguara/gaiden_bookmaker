from django.apps import apps
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from .models import CorpusBatch, ProjectCorpus
from .services import CorpusCatalogService, batch_status, save_selection


@staff_member_required
@require_GET
def catalog(request):
    service = CorpusCatalogService()
    authors = service.works().values("author_id", "author__name").annotate(work_count=Count("pk")).order_by("author__name")
    works = service.works().filter(author_id=request.GET.get("author")) if request.GET.get("author", "").isdigit() else service.works().none()
    return render(request, "corpus/catalog.html", {"authors": authors,
        "rows": [service.status(w) for w in works],
        "batches": [batch_status(b) for b in CorpusBatch.objects.order_by("code")],
        "projects": ProjectCorpus.objects.select_related("project").order_by("project_id")})


@staff_member_required
@require_http_methods(["GET", "POST"])
def project_selection(request, project_id):
    project = get_object_or_404(apps.get_model("writer", "StoryProject"), pk=project_id)
    config = get_object_or_404(ProjectCorpus, project=project)
    if request.method == "POST":
        try:
            ids = {int(value) for value in request.POST.getlist("works")}
            scope = {int(value) for value in request.POST.getlist("scope")}
            save_selection(project, ids, scope=scope)
            messages.success(request, "Selecao do corpus salva.")
        except (ValueError, TypeError):
            messages.error(request, "Selecao invalida ou projeto com backend legado.")
        return redirect("corpus:project", project_id=project.pk)
    selected = set(config.works.values_list("pk", flat=True))
    service = CorpusCatalogService()
    authors = service.works().values("author_id", "author__name").annotate(work_count=Count("pk")).order_by("author__name")
    works = service.works().filter(author_id=request.GET.get("author")) if request.GET.get("author", "").isdigit() else config.works.select_related("author")
    rows = [dict(service.status(work), selected=work.pk in selected) for work in works]
    return render(request, "corpus/selection.html", {"project": project, "config": config,
        "authors": authors, "rows": rows, "selected_count": len(selected)})
