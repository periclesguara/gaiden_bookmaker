import logging
import json
import subprocess
import tempfile
from functools import lru_cache
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib import messages
from django.conf import settings
from django.core.exceptions import ValidationError
from django.forms import HiddenInput
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .forms import AutomaticCoverPDFForm, BACK_COVER_ELEMENTS, CoverDesignForm, EditionConfigurationForm, EditionForm, InteriorSettingsForm
from .models import CoverBuild, InteriorBuild, PreflightReport, PrintEdition, PrintExport
from . import services, catalog, epub_preview
from zipfile import ZipFile, BadZipFile
from lxml.etree import XMLSyntaxError
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)


COVER_PREVIEW_FONTS = {
    "eb-garamond": "EB Garamond",
    "gfs-didot": "GFS Didot",
    "gfs-bodoni": "GFS BodoniClassic",
    "gfs-baskerville": "GFS Baskerville",
    "berenis-adf-pro": "Berenis ADF Pro",
    "accanthis-adf": "Accanthis ADF Std",
    "gentium-book-plus": "Gentium Book Plus",
    "charis-sil": "Charis SIL",
    "noto-serif-display": "Noto Serif Display",
    "fira-sans": "Fira Sans",
}


def _cover_stage_post_data(request, edition):
    """Keep fields from later, hidden stages from blocking the current save."""
    requested_tab = request.POST.get("continue_to", "")
    current_stage = request.POST.get("current_stage", "")
    if current_stage not in {"front", "back", "spine", "montage"}:
        current_stage = {
            "front": "front", "back": "front", "spine": "back", "montage": "spine",
        }.get(requested_tab, "montage")
    future_prefixes = {
        "front": ("back_", "spine_"),
        "back": ("spine_",),
    }.get(current_stage, ())
    if not future_prefixes:
        return request.POST

    data = request.POST.copy()
    saved = edition.cover_settings or {}
    file_fields = {"cover_image", "back_image", "spine_image", "template"}
    for name, field in CoverDesignForm.base_fields.items():
        if name in file_fields or not name.startswith(future_prefixes):
            continue
        try:
            field.clean(data.get(name))
        except ValidationError:
            replacement = saved.get(name, field.initial)
            if callable(replacement):
                replacement = replacement()
            if replacement is None:
                data.pop(name, None)
            elif isinstance(replacement, bool):
                data[name] = "on" if replacement else ""
            else:
                data[name] = str(replacement)
    return data


@lru_cache(maxsize=len(COVER_PREVIEW_FONTS))
def _cover_preview_font_path(family):
    """Resolve only allowlisted system fonts used by the local cover renderer."""
    try:
        result = subprocess.run(
            ["fc-match", "-f", "%{file}", family],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Http404("Fonte de prévia indisponível.") from exc
    path = Path(result.stdout.strip()).resolve()
    if path.suffix.lower() not in {".otf", ".ttf"} or not path.is_file():
        raise Http404("Fonte de prévia indisponível.")
    return path


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
    # Legacy entrypoint kept for existing bookmarks and integrations. The
    # dedicated /configuration/ URL is used by the new step navigation.
    return configuration(request, pk)


def _edition_context(edition):
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
    return {
        "edition": edition, "source_error": source_error, "interiors": interiors,
        "covers": covers, "reports": reports, "exports": PrintExport.objects.filter(edition=edition),
        "interior": interior, "cover": cover,
        "configuration_form": EditionConfigurationForm(instance=edition),
        "workflow_steps": services.workflow_steps(edition),
        "can_export": not source_error and edition.state in {"PRINT_READY", "EXPORTED"} and bool(edition.edition_approved_at),
    }


@require_GET
def configuration(request, pk):
    edition = get_object_or_404(PrintEdition, pk=pk)
    context = _edition_context(edition)
    context["active_step"] = "configuration"
    context["technical_sheet"] = {
        "Fornecedor": edition.get_platform_display(),
        "Encadernação": edition.get_binding_display(),
        "Formato": edition.get_trim_display(),
        "Papel": edition.get_paper_display(),
        "Tipografia": edition.typography.name,
        "Capa": edition.get_cover_mode_display(),
        "Fonte editorial": f"#{edition.source_id}",
    }
    return render(request, "printbook/configuration.html", context)


@require_GET
def interior_workspace(request, pk):
    edition = get_object_or_404(PrintEdition, pk=pk)
    context = _edition_context(edition)
    context["active_step"] = "interior"
    build = context["interior"]
    preset = services.official_composition_preset(edition, build.page_count if build else None)
    initial = preset["values"] | (edition.composition_settings or {})
    context["interior_form"] = InteriorSettingsForm(initial=initial)
    context["official_preset"] = preset
    if build:
        pages = build.page_count or services.inspect_pdf_page_count(services.root_for(build) / "INTERIOR.pdf")
        try:
            page = int(request.GET.get("page", "1"))
        except ValueError:
            page = 1
        page = max(1, min(page, pages))
        mode = request.GET.get("mode", "spread")
        navigation = preview_navigation("interior", page, pages, mode)
        context.update(navigation)
        context.update({
            "page": page,
            "pages": pages,
            "panels": [preview_panels(build, "interior", number, pages)[0] if number else {"number": None} for number in navigation["numbers"]],
            "thumbnail_pages": range(1, pages + 1),
            "chapters": services.interior_chapters(build),
            "paper_tone": request.GET.get("paper_tone", edition.paper) if request.GET.get("paper_tone", edition.paper) in {"white", "cream", "natural"} else edition.paper,
        })
    return render(request, "printbook/interior.html", context)


def _finding_group(item, edition):
    code = item.get("code", "")
    if code.startswith("interior"):
        category, url_name = "Miolo", "printbook:interior"
    elif code.startswith("cover"):
        category, url_name = "Capa", "printbook:cover"
    elif code.startswith("spec"):
        category, url_name = "Compatibilidade", "printbook:configuration"
    else:
        category, url_name = "Arquivos", "printbook:preflight"
    return {**item, "category": category, "correction_url": url_name, "correction_label": f"Abrir {category.lower()}"}


@require_GET
def preflight_workspace(request, pk):
    edition = get_object_or_404(PrintEdition, pk=pk)
    context = _edition_context(edition)
    context["active_step"] = "preflight"
    report = context["reports"].first()
    context["report"] = report
    context["quality_readiness"] = services.quality_readiness(
        edition, context["interior"], context["cover"], source_error=context["source_error"]
    )
    latest_proof = context["covers"].filter(status__in=["DRAFT", "BLOCKED"]).first()
    context["latest_proof"] = latest_proof
    context["proof_findings"] = [
        _finding_group(item, edition) for item in latest_proof.findings
        if item.get("severity") in {"WARNING", "BLOCKER", "ERROR"}
    ] if latest_proof else []
    context["presented_findings"] = [_finding_group(item, edition) for item in report.findings] if report else []
    context["has_blockers"] = bool(report and any(item.get("severity") in {"BLOCKER", "ERROR"} for item in report.findings))
    return render(request, "printbook/preflight.html", context)


@require_GET
def export_workspace(request, pk):
    edition = get_object_or_404(PrintEdition, pk=pk)
    context = _edition_context(edition)
    context["active_step"] = "export"
    context["report"] = context["reports"].filter(status="READY").first()
    return render(request, "printbook/export.html", context)


@require_GET
def cover_dashboard(request, pk):
    """Dedicated cover-confection workspace for one finalized print edition."""
    edition = get_object_or_404(PrintEdition, pk=pk)
    source_error = ""
    try:
        services.guard(edition)
    except ValueError as exc:
        source_error = str(exc)

    interior = InteriorBuild.objects.filter(edition=edition, status="READY", page_count__gt=0).first()
    cover = CoverBuild.objects.filter(edition=edition, status="READY").first()
    latest_visual = CoverBuild.objects.filter(edition=edition, status__in=["READY", "DRAFT", "BLOCKED"]).first()
    estimated_spine = services.estimated_spine_width(edition, interior.page_count) if interior else None
    stapled_uiclap = bool(interior and services.is_uiclap_stapled(edition, interior.page_count))
    geometry = (services.estimated_cover_geometry(edition, interior.page_count) if stapled_uiclap else (
        latest_visual.geometry if latest_visual else (
            services.estimated_cover_geometry(edition, interior.page_count) if interior else {}
        )
    ))
    panel_width_in = (
        Decimal(str(geometry["trim_width_in"])) + Decimal(str(geometry["bleed_in"]))
        if geometry else None
    )
    design_initial = {
        "title": edition.name,
        "subtitle": edition.source.edition.subtitle,
        "author": edition.source.edition.author or getattr(getattr(edition.source.edition.work, "author", None), "name", ""),
        "language": edition.source.edition.language.native_name,
        "publisher": edition.source.edition.publisher or edition.source.edition.imprint_name,
        "cover_font_family": "EB Garamond",
        "spine_text": edition.name,
        "spine_title": edition.name,
        "spine_author": edition.source.edition.author or getattr(getattr(edition.source.edition.work, "author", None), "name", ""),
        "spine_publisher": edition.source.edition.publisher or edition.source.edition.imprint_name,
        "back_footer": "Coleção Mestres Estoicos · RinoBooks",
        "spine": estimated_spine,
    }
    saved = services.load_cover_design(latest_visual)
    saved.update(edition.cover_settings or {})
    design_initial.update({key: value for key, value in saved.items() if key in CoverDesignForm.base_fields})
    if edition.platform == "uiclap":
        design_initial.setdefault("enhancement_profile", "uiclap_300")
    if stapled_uiclap:
        design_initial["spine"] = 0
    professional_fonts = {value for value, _label in CoverDesignForm.COVER_FONTS}
    if design_initial.get("cover_font_family") not in professional_fonts:
        design_initial["cover_font_family"] = "EB Garamond"
    for field_name in (
        "front_title_font_family", "front_subtitle_font_family",
        "front_author_font_family", "front_language_font_family", "front_publisher_font_family",
    ):
        if design_initial.get(field_name) not in professional_fonts:
            design_initial[field_name] = design_initial["cover_font_family"]
    if design_initial.get("spine_font_family") not in professional_fonts:
        design_initial["spine_font_family"] = design_initial["cover_font_family"]
    for key, label, size, y in BACK_COVER_ELEMENTS:
        prefix = f"back_element_{key}"
        defaults = {"font_family": design_initial["cover_font_family"], "font_size_pt": size,
                    "weight": "bold" if key == "heading" else "regular",
                    "style": "italic" if key == "quote" else "normal", "letter_spacing_pt": 0,
                    "color": design_initial.get("back_accent_color" if key in {"heading", "quote"} else "back_text_color", "#D4A24C" if key in {"heading", "quote"} else "#FFFFFF"),
                    "x_percent": 50, "y_percent": y}
        for suffix, value in defaults.items():
            design_initial.setdefault(f"{prefix}_{suffix}", value)
    design_initial["save_mode"] = "draft"
    design_form = CoverDesignForm(initial=design_initial)
    if stapled_uiclap:
        design_form.fields["spine_image"].widget.attrs["disabled"] = True
    back_elements = [{"key": key, "label": label, "prefix": f"back_element_{key}",
                      "fields": [design_form[f"back_element_{key}_{suffix}"] for suffix in
                                 ("font_family", "font_size_pt", "weight", "style", "letter_spacing_pt", "color", "x_percent", "y_percent")]}
                     for key, label, _size, _y in BACK_COVER_ELEMENTS]
    cover_font_faces = [
        {
            "slug": slug,
            "family": family,
            "css_family": f"Gaiden-{slug}",
            "format": "opentype" if _cover_preview_font_path(family).suffix.lower() == ".otf" else "truetype",
        }
        for slug, family in COVER_PREVIEW_FONTS.items()
    ]
    return render(request, "printbook/cover.html", {
        "edition": edition,
        "active_step": "cover",
        "workflow_steps": services.workflow_steps(edition),
        "source_error": source_error,
        "interior": interior,
        "cover": cover,
        "latest_visual": latest_visual,
        "saved_design": saved,
        "covers": CoverBuild.objects.filter(edition=edition),
        "geometry": geometry,
        "panel_width_in": panel_width_in,
        "estimated_spine": estimated_spine,
        "design_form": design_form,
        "automatic_cover_form": AutomaticCoverPDFForm(),
        "back_elements": back_elements,
        "cover_mode": edition.cover_mode,
        "stapled_uiclap": stapled_uiclap,
        "cover_font_faces": cover_font_faces,
    })


@require_GET
def cover_preview_font(request, slug):
    family = COVER_PREVIEW_FONTS.get(slug)
    if not family:
        raise Http404("Fonte de prévia indisponível.")
    path = _cover_preview_font_path(family)
    content_type = "font/otf" if path.suffix.lower() == ".otf" else "font/ttf"
    response = FileResponse(path.open("rb"), content_type=content_type)
    response["Cache-Control"] = "public, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
def cover_source_image(request, pk):
    return _cover_design_image(request, pk, "cover_image")


@require_GET
def cover_back_source_image(request, pk):
    return _cover_design_image(request, pk, "back_image")


@require_GET
def cover_spine_source_image(request, pk):
    return _cover_design_image(request, pk, "spine_image")


def _cover_design_image(request, pk, file_key):
    build = get_object_or_404(CoverBuild, pk=pk, status__in=["DRAFT", "BLOCKED", "READY"])
    services.guard(build.edition)
    item = build.files.get(file_key)
    if not item:
        raise Http404
    try:
        path = services.verified_build_file(build, item)
    except ValueError:
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
        {
            "ok": cover.status == "READY" and bool(cover.files.get("template") or cover.files.get("configured_cover")),
            "label": "Geometria da capa conferida e capa final salva",
        },
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
        try:
            path = services.verified_build_file(build, item)
        except ValueError:
            raise Http404("Template inválido.")
    except ValueError as exc:
        raise Http404("Build invalidado.") from exc
    return FileResponse(path.open("rb"), as_attachment=False, filename="OFFICIAL_TEMPLATE.pdf", content_type="application/pdf")


@require_POST
def action(request, pk, action):
    edition = get_object_or_404(PrintEdition, pk=pk)
    cover_continue_to = ""
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
            after = services.snapshot(edition)
            if after != before:
                services.invalidate(edition)
                edition.state = PrintEdition.State.CONFIGURED
                edition.save(update_fields=["state", "updated_at"])
                services.record_configuration_history(edition, before, after)
        elif action == "interior_settings":
            form = InteriorSettingsForm(request.POST)
            if not form.is_valid():
                for errors in form.errors.values():
                    for error in errors:
                        messages.error(request, error)
                return redirect("printbook:interior", pk=pk)
            services.save_composition(edition, form.cleaned_data)
        elif action == "apply_official_preset":
            current = InteriorBuild.objects.filter(edition=edition, status="READY").first()
            preset = services.official_composition_preset(edition, current.page_count if current else None)
            services.save_composition(edition, preset["values"])
        elif action == "compile":
            services.compile_interior(edition)
        elif action == "approve_interior":
            services.approve_interior(edition)
        elif action == "automatic_cover_pdf":
            form = AutomaticCoverPDFForm(request.POST, request.FILES)
            if not form.is_valid():
                for errors in form.errors.values():
                    for error in errors:
                        messages.error(request, error)
                response = redirect("printbook:cover", pk=pk)
                response["Location"] = f'{response["Location"]}?tab=automatic'
                return response
            services.import_automatic_cover_pdf(edition, form.cleaned_data["cover_pdf"])
            messages.success(request, "Capa automática validada. Contracapa e lombada foram dispensadas; prossiga ao preflight.")
            response = redirect("printbook:preflight", pk=pk)
            return response
        elif action == "design_cover":
            requested_tab = request.POST.get("continue_to", "")
            form = CoverDesignForm(_cover_stage_post_data(request, edition), request.FILES)
            if not form.is_valid():
                if form.cleaned_data:
                    services.save_cover_draft_fields(edition, form.cleaned_data)
                for field, errors in form.errors.items():
                    label = form.fields[field].label if field in form.fields else "Formulário"
                    for error in errors:
                        messages.error(request, f"{label}: {error}")
                messages.warning(request, "Os demais campos válidos foram preservados no rascunho.")
                response = redirect("printbook:cover", pk=pk)
                current_tab = {"back": "front", "spine": "back", "montage": "spine"}.get(requested_tab)
                if current_tab:
                    response["Location"] = f'{response["Location"]}?tab={current_tab}&saved=partial'
                return response
            services.save_cover_draft_fields(edition, form.cleaned_data)
            services.design_cover(edition, **form.cleaned_data)
            if requested_tab in {"front", "back", "spine", "montage"}:
                cover_continue_to = requested_tab
        elif action == "approve_cover":
            services.approve_cover(edition)
        elif action == "preflight":
            services.preflight(edition)
        elif action == "approve_visual_review":
            services.approve_visual_review(edition)
        elif action == "approve_edition":
            services.approve_edition(edition)
        elif action == "export":
            services.export_package(edition)
        else:
            raise Http404
        if action == "design_cover" and cover_continue_to:
            messages.success(request, "Rascunho da capa salvo. Continue na próxima etapa.")
        else:
            messages.success(request, "Operação registrada. Confira o estado e o relatório do build.")
    except (ValueError, RuntimeError) as exc:
        # Compiler output may contain operator paths; keep it out of the page.
        if isinstance(exc, ValueError):
            messages.error(request, str(exc))
        else:
            messages.error(request, "Ferramenta de produção indisponível ou build falhou. Consulte o administrador.")
    except (OSError, subprocess.SubprocessError):
        messages.error(request, "Falha de arquivo ou ferramenta externa; build não aprovado.")
    destinations = {
        "configure": "printbook:configuration",
        "interior_settings": "printbook:interior",
        "apply_official_preset": "printbook:interior",
        "compile": "printbook:interior",
        "approve_interior": "printbook:interior",
        "design_cover": "printbook:cover",
        "automatic_cover_pdf": "printbook:preflight",
        "approve_cover": "printbook:cover",
        "preflight": "printbook:preflight",
        "approve_visual_review": "printbook:preflight",
        "approve_edition": "printbook:preflight",
        "export": "printbook:export",
    }
    response = redirect(destinations.get(action, "printbook:configuration"), pk=pk)
    if action == "design_cover" and cover_continue_to:
        response["Location"] = f'{response["Location"]}?tab={cover_continue_to}&saved=draft'
    return response


MODELS = {"interior": InteriorBuild, "cover": CoverBuild, "report": PreflightReport, "export": PrintExport}


def artifact(build):
    services.guard(build.edition)
    build.refresh_from_db()
    if build.status != "READY" and not (
        isinstance(build, CoverBuild) and build.status in {"DRAFT", "BLOCKED"}
    ):
        raise Http404("Artefato não está atual.")
    item = build.files.get("pdf") or build.files.get("report") or build.files.get("package")
    try:
        path = services.verified_build_file(build, item)
    except ValueError:
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
