from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.urls import reverse

from .forms import (CorpusSelectionForm, WriterIntakeForm, ChapterForm, GoogleDocSourceForm, ManuscriptUploadForm, ReferenceBibleForm,
                    ProjectReferenceForm, ProjectReferenceSelectionForm, ProjectSourcesForm, StoryProjectForm)
from .models import Chapter, ManuscriptRevision, ProjectReference, SourceDocument, StoryProject, WriterExport
from .services.dashboard import build_project_dashboard
from .services.generation import generate_chapter
from .services.narrative import run_chapter_workflow
from .services.normalization import normalize_document
from .services.projects import save_reference_bible, synchronize_chapters
from .services.sources import discover_source_documents, register_google_doc
from .services.references import set_project_references, vectorize_project_references, vectorize_source
from .services.chapter_closure import close_chapter
from .services.package_preparation import prepare_chapter_package
from .services.google_drive_export import export_chapter_package
from .services.output_import import import_chapter_output
from .services.project_export import export_project_to_drive, import_return_files, scan_return_files
from .services.manuscript_pipeline import (
    save_merged_manuscript,
)
from .services.finalization import (FALLBACK_WARNING, edition_source, export_merge as export_merge_service,
                                   latest_merge, prepare_edition_source, save_final as save_final_service,
                                   saved_final)
from .services.manuscript_drive import import_drive_final, list_final_files
from .services.google_docs import canonical_google_doc_url, extract_document_id
from .services.manuscripts import (
    close_writer_project,
    ensure_project_writable,
    import_manuscript_revision,
    promote_canonical_manuscript as promote_canonical_manuscript_service,
    ProjectClosedError,
)
from .language_suggestions import LANGUAGE_SUGGESTIONS
from .services.reference_catalog import author_catalog, works_for_author, work_queryset, serialize_work
from .services.corpus import prepare_project_corpus, save_corpus_selection


@require_GET
def home(request: HttpRequest) -> HttpResponse:
    projects = StoryProject.objects.prefetch_related("chapters", "references__source__writer_vector_index")
    return render(request, "writer/home.html", {
        "projects": [
            {"project": project, "rag_ready": build_project_dashboard(project, probe_models=False)["sources"]["all_vectorized"]}
            for project in projects
        ],
        "source_count": SourceDocument.objects.count(),
    })


@require_GET
def work_catalog(request: HttpRequest) -> JsonResponse:
    author_id = request.GET.get("author", "").strip()
    if author_id and not author_id.isdigit():
        return JsonResponse({"results": [], "next_page": None}, status=400)
    if author_id and request.GET.get("all") == "1":
        ids = list(work_queryset().filter(author_id=int(author_id)).order_by("pk").values_list("pk", flat=True)[:501])
        if len(ids) > 500:
            return JsonResponse({"error": "Selecione até 500 obras por vez."}, status=400)
        return JsonResponse({"work_ids": ids})
    return JsonResponse(works_for_author(int(author_id) if author_id else None, request.GET.get("q", "").strip(), request.GET.get("page", 1)))


@require_GET
def reference_authors(request: HttpRequest) -> JsonResponse:
    return JsonResponse(author_catalog(request.GET.get("q", "").strip(), request.GET.get("page", 1)))


@require_GET
def library(request: HttpRequest) -> HttpResponse:
    from .services.library import existing_library
    return render(request, "writer/library.html", existing_library())


@require_GET
def library_browse(request: HttpRequest) -> HttpResponse:
    from gaiden.infrastructure.drive_storage import DrivePathError, DriveStorageError, RcloneDriveStorage
    try:
        entries = RcloneDriveStorage().list_library_entries(request.GET.get("folder", ""))
    except DrivePathError:
        return JsonResponse({"error": "Pasta inválida: use a entrada ou o armazenamento importado."}, status=400)
    except (DriveStorageError, ValueError):
        return JsonResponse({"error": "Não foi possível consultar esta pasta no Drive. Os arquivos existentes foram preservados."}, status=503)
    return JsonResponse({"entries": entries})


@require_GET
def sources(request: HttpRequest) -> HttpResponse:
    documents = list(SourceDocument.objects.all())
    normalized = sum(
        document.status in {SourceDocument.Status.NORMALIZED, SourceDocument.Status.VECTORIZED}
        and bool(document.normalized_path and document.normalized_sha256)
        for document in documents
    )
    projects = StoryProject.objects.filter(references__enabled=True).distinct().prefetch_related(
        "references__source__edition__work__author",
        "references__intake_work__author",
        "chapters",
    )
    project_rows = [
        {"project": project, "dashboard": build_project_dashboard(project, probe_models=False)}
        for project in projects
    ]
    return render(request, "writer/sources.html", {
        "documents": documents,
        "normalized_count": normalized,
        "normalization_complete": bool(documents) and normalized == len(documents),
        "project_rows": project_rows,
    })


@require_http_methods(["GET", "POST"])
def google_doc_source_add(request: HttpRequest) -> HttpResponse:
    form = GoogleDocSourceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        document = register_google_doc(form.cleaned_data["google_doc"])
        messages.success(request, f"Google Docs registrado: {document.filename}.")
        return redirect("writer:sources")
    return render(request, "writer/google_doc_source_form.html", {"form": form})


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
def normalize_sources(request: HttpRequest, project_id: int | None = None) -> HttpResponse:
    if project_id is not None:
        project = get_object_or_404(StoryProject, pk=project_id)
        from corpus.writer_adapter import is_canonical
        if is_canonical(project):
            return redirect("corpus:catalog")
        result = prepare_project_corpus(project)
        messages.info(request, f"Normalização: {result['prepared']} nova(s), {result['reused']} já disponível(is), {result['pending']} pendente(s).")
        if request.POST.get("next") == "sources":
            return redirect("writer:sources")
        return redirect("writer:project_detail", project_id=project.pk)
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
    if project.pk:
        try:
            ensure_project_writable(project)
        except ProjectClosedError:
            if request.method == "GET":
                return redirect("writer:project_detail", project_id=project.id)
            raise
    form = StoryProjectForm(request.POST or None, instance=project)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            project = form.save()
            synchronize_chapters(project)
        messages.success(request, "Projeto e tabela de capítulos salvos.")
        return redirect("writer:project_detail", project_id=project.id)
    selected = []
    raw = form.reference_rows
    work_ids = [row.get("work_id") for row in raw if isinstance(row, dict) and str(row.get("work_id", "")).isdigit()]
    works_by_id = {work.pk: work for work in work_queryset().filter(pk__in=work_ids)}
    for row in raw:
        work = works_by_id.get(row.get("work_id")) if isinstance(row, dict) else None
        if not work:
            continue
        selected.append({**serialize_work(work), "role": row.get("reference_role", "OTHER"), "notes": row.get("notes", "")})
    sections = [
        ("História e personagens", ["premise", "character_bible", "supporting_characters_bible", "antagonist_bible"]),
        ("Mundo e cenários", ["scenario_bible", "world_bible"]),
    ]
    return render(request, "writer/project_form.html", {
        "form": form, "project": project, "selected_works": selected,
        "editorial_sections": [(title, [form[name] for name in names]) for title, names in sections],
        "language_suggestions": LANGUAGE_SUGGESTIONS,
    })


@require_GET
def project_detail(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(
        StoryProject.objects.prefetch_related("chapters__sessions", "sources", "references__source__edition__work__author", "references__intake_work__author"), pk=project_id
    )
    dashboard = build_project_dashboard(project)
    chapters = list(project.chapters.order_by("number"))
    all_chapters_final = (
        len(chapters) == project.chapter_count
        and all(chapter.status == Chapter.Status.FINAL and chapter.final_text.strip() for chapter in chapters)
    )
    selected = project.manuscript_revisions.filter(source_kind=ManuscriptRevision.SourceKind.IMPORTED_FINAL)
    if request.GET.get("selected_final", "").isdigit():
        selected = selected.filter(pk=int(request.GET["selected_final"]))
    selected = selected.order_by("-version").first()
    merge = latest_merge(project)
    final = saved_final(project)
    exports_by_chapter = {}
    for writer_export in project.writer_exports.order_by("created_at"):
        for expected in writer_export.expected_files:
            exports_by_chapter[str(expected.get("chapter_id", ""))] = writer_export
    for row in dashboard["chapter_rows"]:
        chapter = row["chapter"]
        writer_export = exports_by_chapter.get(str(chapter.pk))
        row["writer_export"] = writer_export
        if writer_export:
            row["writer_transfer"] = _chapter_transfer(chapter, writer_export, row.get("summary"))
    return render(request, "writer/project_detail.html", {
        "project": project,
        "reference_bible_form": ReferenceBibleForm(instance=project),
        "selected_works": [{"work_id": ref.intake_work_id or ref.source.edition.work_id,
                            "title": (ref.intake_work or ref.source.edition.work).title,
                            "author_id": (ref.intake_work or ref.source.edition.work).author_id,
                            "author": (ref.intake_work or ref.source.edition.work).author.name}
                           for ref in project.references.filter(enabled=True).select_related("intake_work__author", "source__edition__work__author")
                           if ref.intake_work_id or ref.source_id],
        "dashboard": dashboard,
        "workflow_stages": dashboard["stages"],
        "manuscript_revisions": project.manuscript_revisions.all(),
        "latest_writer_export": project.writer_exports.order_by("-created_at").first(),
        "manuscript_upload_form": ManuscriptUploadForm(),
        "writer_intake_form": WriterIntakeForm(),
        "manuscript_pipeline": {
            "all_chapters_final": all_chapters_final,
            "merge_revision": merge,
            "selected_final": selected,
            "final_revision": final,
            "edition_revision": edition_source(project),
            "can_enter_edition": bool(project.canonical_manuscript) if project.status == StoryProject.Status.CLOSED else bool(final or merge or all_chapters_final),
            "fallback_warning": FALLBACK_WARNING if merge and not final else "",
        },
    })


@require_POST
def project_export_drive(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    ensure_project_writable(project)
    try:
        chapter_id = request.POST.get("chapter_id", "")
        chapter = get_object_or_404(project.chapters, pk=chapter_id) if chapter_id else None
        export = export_project_to_drive(project, chapter=chapter)
        chapter_number = export.expected_files[0]["chapter_number"]
        messages.success(request, f"CAPÍTULO {chapter_number:02d} EXPORTADO · Export ID {export.export_id}.")
    except Exception as exc:
        if "Google credentials unavailable" in str(exc):
            messages.error(request, "Pacote não enviado ao Google Drive: autenticação expirada. Renove o login e tente novamente.")
        else:
            messages.error(request, f"Exportação não concluída: {exc}")
    return redirect("writer:project_detail", project_id=project.pk)


@require_GET
def project_export_contract(request: HttpRequest, project_id: int, export_id: str) -> HttpResponse:
    export = get_object_or_404(WriterExport, project_id=project_id, export_id=export_id)
    return HttpResponse(export.clipboard_contract, content_type="text/plain; charset=utf-8")


@require_POST
def project_export_check_return(request: HttpRequest, project_id: int, export_id: str) -> HttpResponse:
    export = get_object_or_404(WriterExport.objects.select_related("project"), project_id=project_id, export_id=export_id)
    ensure_project_writable(export.project)
    try:
        files = scan_return_files(export)
        messages.info(request, f"Return verificado: {len(files)} arquivo(s) esperado(s) presente(s).")
    except Exception as exc:
        messages.error(request, f"Verificação do return falhou: {exc}")
    return redirect("writer:project_detail", project_id=project_id)


@require_POST
def project_export_import_return(request: HttpRequest, project_id: int, export_id: str) -> HttpResponse:
    export = get_object_or_404(WriterExport.objects.select_related("project"), project_id=project_id, export_id=export_id)
    ensure_project_writable(export.project)
    try:
        result = import_return_files(export)
        messages.success(request, f"Return importado: {result['imported']} novo(s), {result['skipped']} duplicado(s), {result['found']} encontrado(s). Revise antes de finalizar.")
    except Exception as exc:
        messages.error(request, f"Importação do return não concluída: {exc}")
    return redirect("writer:project_detail", project_id=project_id)


@require_POST
def project_reference_bible(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    ensure_project_writable(project)
    form = ReferenceBibleForm(request.POST, instance=project)
    if "reference_bible" in request.POST and form.is_valid():
        save_reference_bible(project, form.cleaned_data["reference_bible"])
        messages.success(request, "Bíblia de Referências salva.")
    else:
        messages.error(request, "Não foi possível salvar a Bíblia de Referências. Envie um texto válido.")
    return redirect(f"{reverse('writer:project_detail', args=[project.pk])}#reference-bible")


@require_POST
def project_references(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    ensure_project_writable(project)
    from corpus.writer_adapter import is_canonical
    if is_canonical(project):
        return redirect("corpus:project", project_id=project.pk)
    if "work_ids" in request.POST or "remove_work_ids" in request.POST or request.POST.get("corpus_selection") == "1":
        form = CorpusSelectionForm(request.POST)
        if form.is_valid():
            save_corpus_selection(project, form.cleaned_data["work_ids"], form.cleaned_data["remove_work_ids"])
            messages.success(request, "Corpus do Projeto salvo.")
        else:
            messages.error(request, "Seleção de obras inválida. O corpus foi preservado.")
    else:
        form = ProjectReferenceSelectionForm(request.POST, project=project)
        if form.is_valid():
            set_project_references(project, form.cleaned_data["sources"])
            messages.success(request, "Corpus do Projeto salvo.")
        else:
            messages.error(request, "Seleção de referências inválida.")
    return redirect("writer:project_detail", project_id=project.pk)


@require_POST
def project_reference_edit(request: HttpRequest, project_id: int, reference_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    ensure_project_writable(project)
    reference = get_object_or_404(ProjectReference, pk=reference_id, project=project)
    form = ProjectReferenceForm(request.POST, instance=reference)
    if form.is_valid():
        form.save()
        messages.success(request, "Papéis e notas da referência salvos.")
    else:
        messages.error(request, "Papéis ou notas inválidos.")
    return redirect("writer:project_detail", project_id=project.pk)


@require_POST
def project_reference_remove(request: HttpRequest, project_id: int, reference_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    ensure_project_writable(project)
    get_object_or_404(ProjectReference, pk=reference_id, project=project).delete()
    messages.success(request, "Referência removida somente deste projeto; a fonte do Intake foi preservada.")
    return redirect("writer:project_detail", project_id=project.pk)


@require_POST
def reference_vectorize(request: HttpRequest, project_id: int, reference_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    ensure_project_writable(project)
    reference = get_object_or_404(ProjectReference.objects.select_related("source"), pk=reference_id, project=project)
    if not reference.source_id:
        messages.warning(request, "Esta obra ainda não possui build do Intake para vetorização.")
        return redirect("writer:project_detail", project_id=project.pk)
    if reference.intake_work_id and reference.source.edition.work_id != reference.intake_work_id:
        messages.error(request, "O build vinculado não pertence à obra. Salve novamente a seleção de referências.")
        return redirect("writer:project_detail", project_id=project.pk)
    try:
        vectorize_source(reference.source)
        messages.success(request, "Fonte canônica vetorizada e disponível para reutilização.")
    except Exception as exc:
        messages.error(request, f"Vetorização falhou: {exc}")
    return redirect("writer:project_detail", project_id=project.pk)


@require_POST
def save_manuscript(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    try:
        revision = save_merged_manuscript(project, request.user)
        messages.success(request, f"MERGE SAVED · v{revision.version}, com todos os capítulos finalizados.")
    except Exception as exc:
        messages.error(request, f"Não foi possível salvar o manuscrito: {exc}")
    return redirect("writer:project_detail", project_id=project.id)


@require_POST
def export_merge(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    try:
        exported = export_merge_service(project)
    except ValueError as exc:
        messages.error(request, f"Exportação não concluída: {exc}")
        return redirect("writer:project_detail", project_id=project.id)
    response = HttpResponse(exported.data, content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{exported.path.name}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_http_methods(["GET", "POST"])
def import_final_drive(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    ensure_project_writable(project)
    if request.method == "POST":
        try:
            revision = import_drive_final(project, request.POST.get("file_id", ""), request.user)
        except ValueError as exc:
            messages.error(request, f"Importação não concluída: {exc}")
        except Exception:
            messages.error(request, "Não foi possível acessar o arquivo do Drive. Verifique a credencial e o compartilhamento.")
        else:
            messages.success(request, f"Selected: {revision.original_filename} · v{revision.version}. Use SAVE FINAL para confirmar.")
            return redirect(f"{reverse('writer:project_detail', args=[project.id])}?{urlencode({'selected_final': revision.id})}#manuscript-finalization")
        return redirect("writer:import_final_drive", project_id=project.id)
    files, next_page, error = [], "", ""
    if project.drive_project_folder_id:
        try:
            files, next_page = list_final_files(project, request.GET.get("page_token"))
        except Exception:
            error = "Não foi possível listar a pasta do projeto. Verifique a credencial, ou informe o link/ID do arquivo."
    return render(request, "writer/import_final_drive.html", {
        "project": project, "files": files, "drive_error": error,
        "next_page_query": urlencode({"page_token": next_page}) if next_page else "",
    })


@require_POST
def save_final(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    raw_id = request.POST.get("revision_id", "")
    if not raw_id.isdigit():
        messages.error(request, "Selecione uma versão final importada.")
        return redirect("writer:project_detail", project_id=project.id)
    revision = get_object_or_404(ManuscriptRevision, pk=int(raw_id), project=project,
                                source_kind=ManuscriptRevision.SourceKind.IMPORTED_FINAL)
    try:
        save_final_service(project, revision)
    except ValueError as exc:
        messages.error(request, f"Final não salvo: {exc}")
    else:
        messages.success(request, f"FINAL MANUSCRIPT SAVED · v{revision.version}.")
    return redirect("writer:project_detail", project_id=project.id)


@require_POST
def proceed_to_intake(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    form = WriterIntakeForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Informe os créditos obrigatórios para abrir os assets do Intake.")
        return redirect("writer:project_detail", project_id=project.id)

    from pipeline.services.writer_intake_handoff import (
        WriterIntakeHandoffError,
        register_closed_writer_in_intake,
        handoff_writer_edition_to_intake,
    )

    try:
        # Persist the frozen revision before the independently retryable Intake handoff.
        # Rolling back its row after publishing immutable files would orphan that version.
        project, used_merge = prepare_edition_source(project, request.user)
        result = register_closed_writer_in_intake(
            project=project,
            author_name=form.cleaned_data["author_name"],
            publisher=form.cleaned_data["publisher"],
        )
        handoff_writer_edition_to_intake(edition=result.edition)
    except (WriterIntakeHandoffError, ValueError) as exc:
        messages.error(request, f"O encaminhamento para o Intake falhou: {exc}")
        return redirect("writer:project_detail", project_id=project.id)

    messages.success(
        request,
        "Edição encaminhada ao Intake." if result.created else "Edição existente encaminhada novamente ao Intake.",
    )
    if used_merge:
        messages.warning(request, FALLBACK_WARNING)
    return redirect(f"{reverse('frontmatter_template_edit', kwargs={'book_code': result.edition.work.code, 'language': result.edition.language.code})}?{urlencode({'return_to': 'writer_intake', 'workflow_edition_id': result.edition.id, 'edition_source': 'writer'})}")


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
    ensure_project_writable(project)
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
    ensure_project_writable(project)
    from corpus.writer_adapter import is_canonical
    if is_canonical(project):
        return redirect("corpus:catalog")
    dashboard = build_project_dashboard(project)
    confirmed = request.POST.get("confirm") == "yes"
    remake = request.POST.get("remake") == "yes"
    if not confirmed and not remake:
        messages.warning(request, "Abra a confirmação antes de iniciar a vetorização.")
        if request.POST.get("next") == "sources":
            return redirect("writer:sources")
        return redirect("writer:project_detail", project_id=project.id)
    if dashboard["sources"]["all_vectorized"] and not remake:
        messages.success(
            request,
            "Vetorização concluída: "
            f"{dashboard['sources']['vectorized']} fontes e "
            f"{dashboard['index']['chunk_count']} trechos no índice.",
        )
        if request.POST.get("next") == "sources":
            return redirect("writer:sources")
        return redirect("writer:project_detail", project_id=project.id)
    if dashboard["vectorize_blockers"]:
        messages.warning(
            request,
            "Normalize as obras pendentes para continuar a vetorização.",
        )
        if request.POST.get("next") == "sources":
            return redirect("writer:sources")
        return redirect("writer:project_detail", project_id=project.id)
    try:
        result = vectorize_project_references(project, force=remake)
        if result["pending"]:
            messages.warning(request, f"Vetorização: {result['vectorized']} novas, {result['reused']} já disponíveis, {len(result['pending'])} pendentes. Consulte os detalhes do corpus.")
            if request.POST.get("next") == "sources":
                return redirect("writer:sources")
            return redirect("writer:project_detail", project_id=project.id)
        if remake:
            messages.success(
                request,
                f"Revetorização concluída: {result['vectorized']} referência(s) processada(s), "
                f"{result['reused']} reutilizada(s).",
            )
        else:
            messages.success(
                request,
                f"Vetorização concluída: {result['vectorized']} nova(s), "
                f"{result['reused']} reutilizada(s) no escopo RAG.",
            )
    except Exception:
        messages.error(request, "Vetorização não concluída. Consulte os detalhes do corpus.")
    if request.POST.get("next") == "sources":
        return redirect("writer:sources")
    return redirect("writer:project_detail", project_id=project.id)


def _chapter_transfer(chapter: Chapter, writer_export: WriterExport, summary) -> dict:
    expected = next((item for item in writer_export.expected_files if str(item.get("chapter_id")) == str(chapter.pk)), {})
    metadata = chapter.generation_metadata or {}
    imported = bool(metadata.get("return_file_id") and metadata.get("writer_export_id") == writer_export.export_id)
    continuity_saved = bool(summary and summary.short_summary.strip())
    if chapter.status == Chapter.Status.FINAL and continuity_saved:
        can_proceed = True
        next_step = "Capítulo seguinte liberado." if chapter.number < chapter.project.chapter_count else "Todos os capítulos foram concluídos; SAVE MERGE pode ser preparado."
    elif chapter.status == Chapter.Status.FINAL:
        can_proceed, next_step = False, "Salvar o resumo de continuidade para liberar o capítulo seguinte."
    elif imported:
        can_proceed, next_step = False, "Revisar e finalizar este capítulo; depois salvar o resumo de continuidade."
    elif expected.get("state") == "EXPORT_ERROR":
        can_proceed, next_step = False, "Renovar a autorização do Google Drive e repetir a exportação com segurança."
    elif expected.get("state") in {"WAITING_RETURN", "EXPORTED"}:
        can_proceed, next_step = False, "Aguardar e importar uma resposta válida da pasta return."
    else:
        can_proceed, next_step = False, "Concluir o fluxo deste capítulo antes de continuar."
    response_file_id = metadata.get("return_file_id", "") if metadata.get("writer_export_id") == writer_export.export_id else ""
    return {
        "state": "IMPORTED" if imported else expected.get("state", writer_export.status),
        "export_id": writer_export.export_id,
        "export_version": expected.get("export_version"),
        "request_url": writer_export.contract_file_url,
        "return_folder_url": writer_export.return_folder_url,
        "return_folder_id": writer_export.return_folder_id,
        "response_filename": metadata.get("return_filename", expected.get("filename", "")) if imported else expected.get("filename", ""),
        "response_file_id": response_file_id,
        "response_file_url": f"https://drive.google.com/file/d/{response_file_id}/view" if response_file_id else "",
        "word_count": metadata.get("word_count") if imported else None,
        "sha256": metadata.get("sha256", "") if imported else "",
        "imported_at": chapter.generated_at if imported else None,
        "imported": imported,
        "can_proceed": can_proceed,
        "next_step": next_step,
        "error_message": writer_export.error_message,
    }


@require_GET
def chapter_detail(request: HttpRequest, chapter_id: int) -> HttpResponse:
    chapter = get_object_or_404(
        Chapter.objects.select_related("project").prefetch_related("sessions"), pk=chapter_id
    )
    writer_export = next((item for item in chapter.project.writer_exports.order_by("-created_at") if any(str(row.get("chapter_id")) == str(chapter.pk) for row in item.expected_files)), None)
    summary = chapter.narrative_summary if hasattr(chapter, "narrative_summary") else None
    return render(request, "writer/chapter_detail.html", {
        "chapter": chapter,
        "writer_export": writer_export,
        "writer_transfer": _chapter_transfer(chapter, writer_export, summary) if writer_export else None,
    })


@require_http_methods(["GET", "POST"])
def chapter_edit(request: HttpRequest, chapter_id: int) -> HttpResponse:
    chapter = get_object_or_404(Chapter.objects.select_related("project"), pk=chapter_id)
    try:
        ensure_project_writable(chapter.project)
    except ProjectClosedError:
        if request.method == "GET":
            return redirect("writer:project_detail", project_id=chapter.project_id)
        raise
    if chapter.status in {Chapter.Status.MERGED, Chapter.Status.FINAL}:
        raise Http404("merged and finalized chapters are immutable")
    form = ChapterForm(request.POST or None, instance=chapter)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Parâmetros do capítulo salvos.")
        return redirect("writer:project_detail", project_id=chapter.project_id)
    return render(request, "writer/chapter_form.html", {"chapter": chapter, "form": form})


@require_POST
def generate(request: HttpRequest, chapter_id: int) -> HttpResponse:
    chapter = get_object_or_404(Chapter.objects.select_related("project"), pk=chapter_id)
    ensure_project_writable(chapter.project)
    if request.POST.get("confirm") != "yes":
        messages.warning(request, "Confirme a preparação do pacote na caixa antes de continuar.")
        return redirect("writer:project_detail", project_id=chapter.project_id)
    dashboard = build_project_dashboard(chapter.project, probe_models=False)
    row = next(
        item for item in dashboard["chapter_rows"] if item["chapter"].pk == chapter.pk
    )
    if not row["generation_ready"]:
        if chapter.status == Chapter.Status.FINAL:
            messages.success(request, "Finalização concluída: este capítulo já está finalizado.")
        elif chapter.status == Chapter.Status.GENERATION_COMPLETE:
            messages.success(
                request, "Geração concluída: revise o capítulo e finalize."
            )
        else:
            messages.warning(
                request, "Geração pendente: " + "; ".join(row["generation_blockers"]) + "."
            )
        return redirect("writer:project_detail", project_id=chapter.project_id)
    try:
        chapter, contract, package_root = prepare_chapter_package(chapter)
        messages.success(request, f"Pacote do Capítulo {chapter.number:02d} preparado; aguardando saída externa.")
    except Exception as exc:
        messages.error(request, f"Falha na geração: {exc}")
    return redirect("writer:chapter_detail", chapter_id=chapter.id)


@require_POST
def generate_next(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(
        StoryProject.objects.prefetch_related("chapters__sessions", "sources"),
        pk=project_id,
    )
    ensure_project_writable(project)
    if request.POST.get("confirm") != "yes":
        messages.warning(request, "Confirme a preparação do pacote do capítulo.")
        return redirect("writer:project_detail", project_id=project.id)
    dashboard = build_project_dashboard(project, probe_models=False)
    row = dashboard["next_chapter_row"]
    if row is None:
        messages.warning(request, "Nenhum próximo capítulo está pronto para geração.")
        return redirect("writer:project_detail", project_id=project.id)
    chapter = row["chapter"]
    if request.POST.get("chapter_id") != str(chapter.pk):
        messages.warning(
            request,
            "O próximo capítulo mudou desde a confirmação; revise o painel e confirme novamente.",
        )
        return redirect("writer:project_detail", project_id=project.id)
    try:
        chapter, contract, package_root = prepare_chapter_package(chapter)
        messages.success(request, f"Pacote do Capítulo {chapter.number:02d} preparado; aguardando saída externa.")
    except Exception as exc:
        messages.error(request, f"Falha na geração do Capítulo {chapter.number:02d}: {exc}")
    return redirect("writer:chapter_detail", chapter_id=chapter.id)


@require_POST
def export_drive(request: HttpRequest, chapter_id: int) -> HttpResponse:
    chapter = get_object_or_404(Chapter, pk=chapter_id)
    ensure_project_writable(chapter.project)
    try:
        chapter = export_chapter_package(chapter)
        messages.success(request, "Pacote exportado para o Google Drive; aguardando saída externa.")
    except Exception as exc:
        messages.error(request, f"Exportação não concluída; o pacote local permanece pronto: {exc}")
    if request.POST.get("return_to") == "project":
        return redirect(f"{reverse('writer:project_detail', kwargs={'project_id': chapter.project_id})}#chapters")
    return redirect("writer:chapter_detail", chapter_id=chapter.id)


@require_POST
def import_output(request: HttpRequest, chapter_id: int) -> HttpResponse:
    chapter = get_object_or_404(Chapter, pk=chapter_id)
    ensure_project_writable(chapter.project)
    try:
        output_url = request.POST.get("output_url", "").strip()
        if output_url:
            output_id = extract_document_id(output_url)
            chapter.drive_output_id = output_id
            chapter.drive_output_url = canonical_google_doc_url(output_id)
            chapter.save(update_fields=("drive_output_id", "drive_output_url", "updated_at"))
        import_chapter_output(chapter)
        messages.success(request, "Saída do capítulo importada; revise antes de finalizar.")
    except Exception as exc:
        messages.error(request, f"Importação não concluída: {exc}")
    if request.POST.get("return_to") == "project":
        return redirect(f"{reverse('writer:project_detail', kwargs={'project_id': chapter.project_id})}#chapters")
    return redirect("writer:chapter_detail", chapter_id=chapter.id)


@require_POST
def finalize(request: HttpRequest, chapter_id: int) -> HttpResponse:
    chapter = get_object_or_404(Chapter, pk=chapter_id)
    ensure_project_writable(chapter.project)
    if chapter.status == Chapter.Status.FINAL and hasattr(chapter, "narrative_summary") and chapter.narrative_summary.short_summary.strip():
        messages.success(request, "Finalização concluída: este capítulo já está finalizado.")
        return redirect("writer:chapter_detail", chapter_id=chapter.id)
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Confirmação editorial obrigatória.")
        return redirect("writer:chapter_detail", chapter_id=chapter.id)
    try:
        with transaction.atomic():
            chapter = Chapter.objects.select_for_update().get(pk=chapter.pk)
            from .services.execution_order import chapter_is_sequentially_eligible
            eligible, reason = chapter_is_sequentially_eligible(chapter, require_plan=False)
            if not eligible:
                raise ValueError(reason)
            close_chapter(chapter)
            if chapter.status != Chapter.Status.FINAL:
                chapter.finalize()
        messages.success(request, "Capítulo finalizado após confirmação editorial.")
    except Exception as exc:
        messages.error(request, f"Não foi possível finalizar: {exc}")
    return redirect("writer:chapter_detail", chapter_id=chapter.id)


@require_POST
def import_manuscript(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    ensure_project_writable(project)
    form = ManuscriptUploadForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            revision = import_manuscript_revision(
                project, form.cleaned_data["manuscript"], request.user
            )
            messages.success(request, f"Manuscrito importado como v{revision.version}.")
            return redirect(f"{reverse('writer:project_detail', args=[project.id])}?{urlencode({'selected_final': revision.id})}#manuscript-finalization")
        except Exception as exc:
            messages.error(request, f"Importação não concluída: {exc}")
    else:
        messages.error(request, "Upload inválido: " + "; ".join(
            error for errors in form.errors.values() for error in errors
        ))
    return redirect("writer:project_detail", project_id=project.id)


@require_POST
def promote_canonical_manuscript(request: HttpRequest, project_id: int, revision_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    ensure_project_writable(project)
    revision = get_object_or_404(ManuscriptRevision, pk=revision_id, project=project)
    try:
        promote_canonical_manuscript_service(project, revision)
        messages.success(request, f"v{revision.version} definido como Miolo Canônico.")
    except Exception as exc:
        messages.error(request, f"Não foi possível definir o canonical: {exc}")
    return redirect("writer:project_detail", project_id=project.id)


@require_POST
def close_project(request: HttpRequest, project_id: int) -> HttpResponse:
    project = get_object_or_404(StoryProject, pk=project_id)
    ensure_project_writable(project)
    if request.POST.get("confirm") != "yes":
        messages.warning(request, "Confirmação obrigatória para fechar o projeto.")
        return redirect("writer:project_detail", project_id=project.id)
    try:
        close_writer_project(project, request.user)
        messages.success(request, "Projeto fechado; o Writer agora é somente leitura.")
    except Exception as exc:
        messages.error(request, f"Não foi possível fechar o projeto: {exc}")
    return redirect("writer:project_detail", project_id=project.id)
