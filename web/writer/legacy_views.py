from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from author_studio.models import Work

from .forms import PromotionForm, VersionForm
from .models import Manuscript, ManuscriptVersion
from .legacy_services import create_version, promote_version


@staff_member_required
def works(request):
    return render(request, "writer/works.html", {"works": Work.objects.select_related("author")})


@staff_member_required
def work_detail(request, work_id):
    work = get_object_or_404(Work.objects.select_related("author"), pk=work_id)
    manuscript, _ = Manuscript.objects.get_or_create(work=work)
    return redirect("writer:manuscript", manuscript_id=manuscript.id)


@staff_member_required
def manuscript_detail(request, manuscript_id):
    manuscript = get_object_or_404(Manuscript.objects.select_related("work", "work__author"), pk=manuscript_id)
    latest = manuscript.versions.first()
    form = VersionForm(request.POST or None, initial={"content": latest.content if latest else ""})
    if request.method == "POST" and form.is_valid():
        version = create_version(manuscript, **form.cleaned_data)
        messages.success(request, f"Version {version.version} saved. The official body was not changed.")
        return redirect("writer:manuscript", manuscript_id=manuscript.id)
    return render(request, "writer/manuscript.html", {"manuscript": manuscript, "versions": manuscript.versions.all(), "form": form})


@staff_member_required
def version_preview(request, manuscript_id, version_id):
    version = get_object_or_404(ManuscriptVersion, pk=version_id, manuscript_id=manuscript_id)
    return render(request, "writer/version_preview.html", {"version": version, "form": PromotionForm()})


@staff_member_required
def promote(request, manuscript_id, version_id):
    version = get_object_or_404(ManuscriptVersion, pk=version_id, manuscript_id=manuscript_id)
    if request.method != "POST":
        return redirect("writer:version_preview", manuscript_id=manuscript_id, version_id=version_id)
    form = PromotionForm(request.POST)
    if form.is_valid():
        try:
            event = promote_version(
                version,
                editor_approval=form.cleaned_data["editor_approval"],
                reason=form.cleaned_data["reason"],
                actor=str(request.user) if request.user.is_authenticated else "anonymous",
            )
        except ValueError as exc:
            form.add_error(None, str(exc))
            return render(request, "writer/version_preview.html", {"version": version, "form": form})
        messages.success(request, f"Official body promotion: {event.outcome}.")
        return redirect("writer:manuscript", manuscript_id=manuscript_id)
    return render(request, "writer/version_preview.html", {"version": version, "form": form})


@staff_member_required
def export_version(request, manuscript_id, version_id):
    version = get_object_or_404(ManuscriptVersion, pk=version_id, manuscript_id=manuscript_id)
    response = HttpResponse(version.content, content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{version.manuscript.work.code}_writer_v{version.version}.txt"'
    return response
