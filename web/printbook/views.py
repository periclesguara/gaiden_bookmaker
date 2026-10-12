import logging
import json
import subprocess
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib import messages
from django.conf import settings
from django.forms import HiddenInput
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .forms import CoverDesignForm, EditionConfigurationForm, EditionForm
from .models import CoverBuild, InteriorBuild, PreflightReport, PrintEdition, PrintExport
from . import services, catalog, epub_preview
from zipfile import ZipFile, BadZipFile
from lxml.etree import XMLSyntaxError
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)


@require_GET
def home(request):
    sources = catalog.catalog()
    return render(request, "printbook/home.html", {
        "sources": sources,
        "editions": PrintEdition.objects.filter(source_id__in=[source.pk for source in sources]).order_by("-id"),
    })


def approved_source(pk):
    source = get_object_or_404(catalog.done_sources(), pk=pk)
    if not catalog.eligible(source):
        raise Http404("PrintBook aceita somente EPUB final DONE com integridade atual.")
    return source


@require_http_methods(["GET", "POST"])
def source_dashboard(request, source_id):
    source = approved_source(source_id)
    initial = {"source": source.pk, "name": source.edition.title or source.edition.work.title}
    data = request.POST.copy() if request.method == "POST" else None
    if data is not None:
        data["source"] = source.pk
    form = EditionForm(data, initial=initial)
    form.fields["source"].widget = HiddenInput()
    if request.method == "POST" and form.is_valid():
        approved_source(source_id)
        edition = form.save(commit=False)
        edition.source_path = source.official_body_path
        edition.source_sha256 = source.official_body_sha256
        edition.save()
        return redirect("printbook:detail", pk=edition.pk)
    return render(request, "printbook/source.html", {
        "source": source, "form": form,
        "editions": PrintEdition.objects.filter(source=source).order_by("-id"),
    })


@require_GET
def source_preview(request, source_id):
    source = approved_source(source_id)
    try:
        pages, _members = epub_preview.package(source._safe_existing_path(source.epub_path, require_epub=True))
    except (OSError, ValueError, KeyError, BadZipFile, XMLSyntaxError) as exc:
        raise Http404("Não foi possível ler o EPUB final.") from exc
    return render(request, "printbook/source_preview.html", {"source": source, "pages": pages})


@require_GET
@xframe_options_sameorigin
def source_asset(request, source_id, member):
    source = approved_source(source_id)
    path = source._safe_existing_path(source.epub_path, require_epub=True)
    try:
        _pages, members = epub_preview.package(path)
        if member not in members:
            raise Http404
        allowed = {"application/xhtml+xml", "text/html", "text/css", "image/png", "image/jpeg",
                   "image/gif", "image/svg+xml", "font/woff", "font/woff2", "font/ttf", "font/otf",
                   "application/vnd.ms-opentype", "application/font-sfnt"}
        if members[member] not in allowed:
            raise Http404
        with ZipFile(path) as archive:
            body = epub_preview.read_member(archive, member)
    except (OSError, ValueError, KeyError, BadZipFile, XMLSyntaxError) as exc:
        raise Http404("Recurso do EPUB indisponível.") from exc
    response = HttpResponse(body, content_type=members[member])
    response["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; base-uri 'none'; form-action 'none'"
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def detail(request, pk):
    edition = get_object_or_404(PrintEdition, pk=pk)
    source_error = ""
    try:
        services.guard(edition)
    except ValueError as exc:
        source_error = str(exc)
    interiors = InteriorBuild.objects.filter(edition=edition)
    covers = CoverBuild.objects.filter(edition=edition)
    reports = PreflightReport.objects.filter(edition=edition)
    interior = interiors.filter(status="READY").first()
    cover = covers.filter(status="READY").first()
    return render(request, "printbook/detail.html", {
        "edition": edition, "source_error": source_error, "interiors": interiors,
        "covers": covers, "reports": reports, "exports": PrintExport.objects.filter(edition=edition),
        "interior": interior, "cover": cover,
        "configuration_form": EditionConfigurationForm(instance=edition),
        "can_export": not source_error and edition.state in {"PRINT_READY", "EXPORTED"},
    })


@require_GET
def cover_dashboard(request, pk):
    """Dedicated cover-confection workspace for one finalized print edition."""
    edition = get_object_or_404(PrintEdition, pk=pk)
    source_error = ""
    try:
        services.guard(edition)
    except ValueError as exc:
        source_error = str(exc)

    interior = InteriorBuild.objects.filter(edition=edition, status="READY").first()
    cover = CoverBuild.objects.filter(edition=edition, status="READY").first()
    latest_visual = CoverBuild.objects.filter(edition=edition, status__in=["READY", "DRAFT", "BLOCKED"]).first()
    estimated_spine = services.estimated_spine_width(edition, interior.page_count) if interior else None
    geometry = latest_visual.geometry if latest_visual else (
        services.estimated_cover_geometry(edition, interior.page_count) if interior else {}
    )
    design_initial = {
        "title": edition.name,
        "author": getattr(getattr(edition.source.edition.work, "author", None), "name", ""),
        "spine_text": edition.name,
        "spine": estimated_spine,
    }
    saved = services.load_cover_design(latest_visual)
    design_initial.update({key: value for key, value in saved.items() if key in CoverDesignForm.base_fields})
    design_initial["save_mode"] = "draft"
    return render(request, "printbook/cover.html", {
        "edition": edition,
        "source_error": source_error,
        "interior": interior,
        "cover": cover,
        "latest_visual": latest_visual,
        "saved_design": saved,
        "covers": CoverBuild.objects.filter(edition=edition),
        "geometry": geometry,
        "estimated_spine": estimated_spine,
        "design_form": CoverDesignForm(initial=design_initial),
    })


@require_GET
def cover_source_image(request, pk):
    build = get_object_or_404(CoverBuild, pk=pk, status__in=["DRAFT", "BLOCKED", "READY"])
    services.guard(build.edition)
    item = build.files.get("cover_image")
    if not item:
        raise Http404
    path = Path(item["path"]).resolve()
    if not path.is_relative_to(services.root_for(build).resolve()) or not path.is_file() or services.sha(path) != item["sha256"]:
        raise Http404
    content_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return FileResponse(path.open("rb"), content_type=content_type)


@require_GET
def book_preview(request, pk):
    edition = get_object_or_404(PrintEdition, pk=pk)
    services.guard(edition)
    interior = InteriorBuild.objects.filter(edition=edition, status="READY").first()
    cover = CoverBuild.objects.filter(edition=edition, status__in=["READY", "DRAFT", "BLOCKED"]).first()
    if not interior or not cover:
        raise Http404("Miolo e capa são necessários para a visualização conjunta.")
    design = services.load_cover_design(cover)
    ppi = int(design.get("effective_image_ppi") or 0)
    readiness = [
        {"ok": interior.status == "READY", "label": f"Miolo atual: {interior.page_count} páginas"},
        {"ok": interior.page_count % 2 == 0, "label": "Paginação final par"},
        {"ok": ppi >= 300, "label": f"Imagem da capa: {ppi or 'não medida'} ppi efetivos"},
        {"ok": cover.status == "READY" and bool(cover.files.get("template")), "label": "Template oficial conferido e capa final salva"},
        {"ok": bool(edition.isbn), "label": "ISBN da edição física informado"},
        {"ok": bool(getattr(settings, "PRINTBOOK_VALIDATOR", "")), "label": "Validador industrial configurado"},
    ]
    return render(request, "printbook/book_preview.html", {
        "edition": edition, "interior": interior, "cover": cover,
        "readiness": readiness,
    })


@require_GET
@xframe_options_sameorigin
def cover_template(request, pk):
    """Open the exact provider template stored with a ready cover build."""
    build = get_object_or_404(CoverBuild, pk=pk)
    try:
        services.guard(build.edition)
        build.refresh_from_db()
        if build.status != "READY":
            raise Http404("Template não está disponível neste build.")
        item = build.files.get("template")
        if not item:
            raise Http404("Este build não guarda um template.")
        path = Path(item["path"]).resolve()
        if not path.is_relative_to(services.root_for(build).resolve()) or not path.is_file() or services.sha(path) != item["sha256"]:
            raise Http404("Template inválido.")
    except ValueError as exc:
        raise Http404("Build invalidado.") from exc
    return FileResponse(path.open("rb"), as_attachment=False, filename="OFFICIAL_TEMPLATE.pdf", content_type="application/pdf")


@require_POST
def action(request, pk, action):
    edition = get_object_or_404(PrintEdition, pk=pk)
    try:
        if action == "configure":
            services.guard(edition)
            before = services.snapshot(edition)
            form = EditionConfigurationForm(request.POST, instance=edition)
            if not form.is_valid():
                for errors in form.errors.values():
                    for error in errors:
                        messages.error(request, error)
                return redirect("printbook:detail", pk=pk)
            edition = form.save()
            if services.snapshot(edition) != before:
                services.invalidate(edition)
                edition.state = PrintEdition.State.CONFIGURED
                edition.save(update_fields=["state", "updated_at"])
        elif action == "compile":
            services.compile_interior(edition)
        elif action == "design_cover":
            form = CoverDesignForm(request.POST, request.FILES)
            if not form.is_valid():
                for field, errors in form.errors.items():
                    label = form.fields[field].label if field in form.fields else "Formulário"
                    for error in errors:
                        messages.error(request, f"{label}: {error}")
                return redirect("printbook:cover", pk=pk)
            services.design_cover(edition, **form.cleaned_data)
        elif action == "preflight":
            services.preflight(edition)
        elif action == "export":
            services.export_package(edition)
        else:
            raise Http404
        messages.success(request, "Operação registrada. Confira o estado e o relatório do build.")
    except (ValueError, RuntimeError) as exc:
        # Compiler output may contain operator paths; keep it out of the page.
        if isinstance(exc, ValueError):
            messages.error(request, str(exc))
        else:
            messages.error(request, "Ferramenta de produção indisponível ou build falhou. Consulte o administrador.")
    except (OSError, subprocess.SubprocessError):
        messages.error(request, "Falha de arquivo ou ferramenta externa; build não aprovado.")
    if action == "design_cover":
        return redirect("printbook:cover", pk=pk)
    return redirect("printbook:detail", pk=pk)


MODELS = {"interior": InteriorBuild, "cover": CoverBuild, "export": PrintExport}


def artifact(build):
    services.guard(build.edition)
    build.refresh_from_db()
    if build.status != "READY" and not (
        isinstance(build, CoverBuild) and build.status in {"DRAFT", "BLOCKED"}
    ):
        raise Http404("Artefato não está atual.")
    item = build.files.get("pdf") or build.files.get("package")
    path = Path(item["path"]).resolve()
    if not path.is_relative_to(services.root_for(build).resolve()) or not path.is_file() or services.sha(path) != item["sha256"]:
        raise Http404
    return path


@require_GET
@xframe_options_sameorigin
def download(request, kind, pk):
    if kind not in MODELS:
        raise Http404
    build = get_object_or_404(MODELS[kind], pk=pk)
    try:
        path = artifact(build)
    except ValueError as exc:
        raise Http404("Build invalidado.") from exc
    return FileResponse(path.open("rb"), as_attachment=kind == "export", filename=path.name)


@require_GET
def preview(request, kind, pk):
    if kind not in {"interior", "cover"}:
        raise Http404
    build = get_object_or_404(MODELS[kind], pk=pk)
    try:
        path = artifact(build)
        page = int(request.GET.get("page", "1"))
        pages = services.inspect_pdf_page_count(path)
        if not 1 <= page <= pages:
            raise ValueError("Página fora do PDF.")
    except ValueError as exc:
        raise Http404(str(exc)) from exc
    try:
        navigation = preview_navigation(kind, page, pages, request.GET.get("mode", "spread"))
        panels = [
            preview_panels(build, kind, number, number)[0] if number else {"number": None}
            for number in navigation["numbers"]
        ]
    except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
        raise Http404("Geometria do preview inválida.") from exc
    return render(request, "printbook/preview.html", {"build": build, "kind": kind,
        "page": page, "pages": pages, **navigation,
        "panels": panels})


def preview_navigation(kind, page, pages, mode):
    """Keep rectos on the right and make every page reachable in either mode."""
    mode = "single" if kind == "cover" or mode == "single" else "spread"
    if mode == "single":
        return {"mode": mode, "numbers": [page],
                "previous": page - 1 if page > 1 else None,
                "next": page + 1 if page < pages else None}
    left = page if page % 2 == 0 else page - 1
    return {"mode": mode,
            "numbers": [left or None, left + 1 if left + 1 <= pages else None],
            "previous": max(1, left - 2) if left else None,
            "next": left + 2 if left + 2 <= pages else None}


def percent(value, total):
    return f"{(Decimal(str(value)) / Decimal(str(total)) * 100):.4f}"


def preview_panels(build, kind, page, pages):
    if kind == "interior":
        spec = services.spec_for(build.edition)
        panels = []
        for number in (page, page + 1):
            if number > pages:
                continue
            left = spec.inner_margin_in if number % 2 else spec.outer_margin_in
            right = spec.outer_margin_in if number % 2 else spec.inner_margin_in
            panels.append({
                "number": number,
                "safe": {
                    "left": percent(left, spec.trim_width_in),
                    "right": percent(right, spec.trim_width_in),
                    "top": percent(spec.top_margin_in, spec.trim_height_in),
                    "bottom": percent(spec.bottom_margin_in, spec.trim_height_in),
                },
            })
        return panels

    geometry = build.geometry
    width = Decimal(str(geometry["spread_width_in"]))
    height = Decimal(str(geometry["spread_height_in"]))
    trim = Decimal(str(geometry["trim_width_in"]))
    trim_height = Decimal(str(geometry["trim_height_in"]))
    spine = Decimal(str(geometry["spine_width_in"]))
    bleed = Decimal(str(geometry["bleed_in"]))
    safe = Decimal("0.25")
    return [{
        "number": 1,
        "trim": {
            "left": percent(bleed, width), "right": percent(bleed, width),
            "top": percent(bleed, height), "bottom": percent(bleed, height),
        },
        "spine_left": percent(bleed + trim, width),
        "spine_right": percent(bleed + trim + spine, width),
        "back_safe": {
            "left": percent(bleed + safe, width),
            "top": percent(bleed + safe, height),
            "width": percent(trim - safe * 2, width),
            "height": percent(trim_height - safe * 2, height),
        },
        "front_safe": {
            "left": percent(bleed + trim + spine + safe, width),
            "top": percent(bleed + safe, height),
            "width": percent(trim - safe * 2, width),
            "height": percent(trim_height - safe * 2, height),
        },
    }]


@require_GET
def page_image(request, kind, pk, page):
    if kind not in {"interior", "cover"}:
        raise Http404
    build = get_object_or_404(MODELS[kind], pk=pk)
    try:
        path = artifact(build)
        if not 1 <= page <= services.inspect_pdf_page_count(path):
            raise Http404
        with tempfile.TemporaryDirectory(prefix="gaiden-print-preview-") as temporary:
            prefix = str(Path(temporary) / "page")
            result = subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile", "-scale-to", "1500", "-png", str(path), prefix], capture_output=True, timeout=30)
            if result.returncode:
                raise ValueError("Falha no preview PDF.")
            return HttpResponse(Path(prefix + ".png").read_bytes(), content_type="image/png")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise Http404("Preview indisponível.") from exc
