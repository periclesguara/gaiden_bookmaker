from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from gaiden.application.intake import (
    clean_downloaded_item,
    confirm_ready_for_editing,
    discover_drive_folder,
    download_drive_item,
    handoff_to_pipeline,
    ingest_many,
    prepare_for_codex,
    register_translation_return,
    store_uploaded_files,
)
from gaiden.domain.intake import IntakeState
from gaiden.infrastructure.intake_drive import RcloneClient

from .forms import (
    DriveSyncForm,
    IntakeBatchForm,
    IntakeItemMetadataForm,
    IntakeUploadForm,
    PrepareCodexForm,
    TranslationReturnForm,
)
from .models import IntakeBatch, IntakeItem


def batch_list(request):
    return render(request, "intake_module/batch_list.html", {"batches": IntakeBatch.objects.all()})


def batch_create(request):
    form = IntakeBatchForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        batch = form.save()
        if "save_only" in request.POST:
            return redirect("intake_module:batch_detail", batch_id=batch.id)
        return redirect("intake_module:batch_files", batch_id=batch.id)
    return render(request, "intake_module/batch_form.html", {"form": form})


def batch_detail(request, batch_id: int):
    batch = get_object_or_404(IntakeBatch, pk=batch_id)
    items = batch.items.all()
    return render(
        request,
        "intake_module/batch_detail.html",
        {
            "batch": batch,
            "items": items,
            "upload_form": IntakeUploadForm(),
            "can_download_next": items.filter(status=IntakeState.DISCOVERED.value).exists(),
            "can_clean_next": items.filter(status=IntakeState.DOWNLOADED.value).exists(),
            "file_summary": request.session.get(_summary_key(batch.id), _empty_summary()),
        },
    )


def item_detail(request, item_id: int):
    item = get_object_or_404(IntakeItem.objects.select_related("batch"), pk=item_id)
    return render(
        request,
        "intake_module/item_detail.html",
        {
            "item": item,
            "metadata_form": IntakeItemMetadataForm(instance=item),
            "prepare_form": PrepareCodexForm(initial={"target_language": item.target_language}),
            "return_form": TranslationReturnForm(),
        },
    )


def batch_upload(request, batch_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    batch = get_object_or_404(IntakeBatch, pk=batch_id)
    form = IntakeUploadForm(request.POST, request.FILES)
    if form.is_valid():
        uploads = request.FILES.getlist("files")
        results = store_uploaded_files(batch, uploads)
        imported = sum(not row.get("ignored") and not row.get("error") for row in results)
        ignored = sum(bool(row.get("ignored")) for row in results)
        duplicates = sum(bool(row.get("duplicate")) for row in results)
        errors = sum(bool(row.get("error")) for row in results)
        _store_summary(
            request,
            batch.id,
            found=len(uploads),
            selected=len(uploads),
            imported=imported,
            ignored=ignored,
            duplicates=duplicates,
            errors=errors,
        )
        messages.success(
            request,
            f"Upload local: {imported} recebidos, {ignored} ignorados, {errors} erros.",
        )
    else:
        messages.error(request, "Selecione um ou mais arquivos válidos.")
    return redirect("intake_module:batch_files", batch_id=batch.id)


def _summary_key(batch_id: int) -> str:
    return f"intake_batch_file_summary_{batch_id}"


def _report_key(batch_id: int) -> str:
    return f"intake_batch_drive_report_{batch_id}"


def _empty_summary() -> dict:
    return {"found": 0, "selected": 0, "imported": 0, "ignored": 0, "duplicates": 0, "errors": 0}


def _store_summary(request, batch_id: int, **values) -> dict:
    summary = {**_empty_summary(), **request.session.get(_summary_key(batch_id), {}), **values}
    request.session[_summary_key(batch_id)] = summary
    return summary


def batch_files(request, batch_id: int):
    batch = get_object_or_404(IntakeBatch, pk=batch_id)
    form = DriveSyncForm(request.POST or None, initial={"relative_folder": batch.drive_relative_path})
    report = request.session.get(_report_key(batch.id))
    try:
        client = RcloneClient()
        remote_name = client.remote
        rclone_available = client.executable_available
        configuration_error = ""
    except Exception as exc:
        client = None
        remote_name = "Configuração inválida"
        rclone_available = False
        configuration_error = str(exc)
    if request.method == "POST" and form.is_valid():
        try:
            if client is None:
                raise ValueError(configuration_error or "Configuração do rclone inválida")
            batch.drive_relative_path = form.cleaned_data["relative_folder"]
            batch.save(update_fields=["drive_relative_path", "updated_at"])
            action = request.POST.get("drive_action")
            if action == "verify":
                client.check_available()
                client.list_folders(form.cleaned_data["relative_folder"])
                messages.success(request, "Pasta verificada com sucesso; nenhum arquivo foi transferido.")
            elif action == "list":
                report = discover_drive_folder(
                    batch,
                    form.cleaned_data["relative_folder"],
                    client=client,
                )
                request.session[_report_key(batch.id)] = report
                _store_summary(
                    request,
                    batch.id,
                    found=len(report["files"]),
                    selected=0,
                    imported=0,
                    ignored=len(report["ignored"]),
                    duplicates=0,
                    errors=len(report["errors"]),
                )
                messages.success(
                    request,
                    f"Drive: {len(report['files'])} encontrados; nenhum arquivo foi transferido.",
                )
            else:
                raise ValueError("Ação do Drive inválida.")
        except Exception as exc:
            messages.error(request, str(exc))
    elif request.method == "POST":
        messages.error(request, "Informe um caminho relativo válido para o Drive.")
    return render(
        request,
        "intake_module/batch_files.html",
        {
            "batch": batch,
            "form": form,
            "upload_form": IntakeUploadForm(),
            "report": report,
            "remote_name": remote_name,
            "rclone_available": rclone_available,
            "configuration_error": configuration_error,
            "file_summary": request.session.get(_summary_key(batch.id), _empty_summary()),
        },
    )


def batch_drive(request, batch_id: int):
    return batch_files(request, batch_id)


def batch_import_selected(request, batch_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    batch = get_object_or_404(IntakeBatch, pk=batch_id)
    raw_ids = request.POST.getlist("selected_items")
    selected_ids = []
    for value in raw_ids:
        try:
            selected_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    selected_ids = list(dict.fromkeys(selected_ids))
    items = list(
        batch.items.filter(
            id__in=selected_ids,
            status=IntakeState.DISCOVERED.value,
        ).order_by("order_index")
    )
    imported = 0
    duplicates = 0
    errors = len(selected_ids) - len(items)
    client = None
    for item in items:
        try:
            client = client or RcloneClient()
            result = download_drive_item(item, client=client)
            imported += 1
            duplicates += int(bool(result.get("duplicate")))
        except Exception as exc:
            errors += 1
            messages.error(request, f"{item.source_filename}: {exc}")
    previous = request.session.get(_summary_key(batch.id), _empty_summary())
    _store_summary(
        request,
        batch.id,
        found=previous.get("found", 0),
        selected=len(selected_ids),
        imported=imported,
        ignored=previous.get("ignored", 0),
        duplicates=duplicates,
        errors=errors,
    )
    messages.success(
        request,
        f"Seleção: {len(selected_ids)}; importados: {imported}; erros: {errors}.",
    )
    return redirect("intake_module:batch_files", batch_id=batch.id)


def item_update_metadata(request, item_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    item = get_object_or_404(IntakeItem.objects.select_related("batch"), pk=item_id)
    form = IntakeItemMetadataForm(request.POST, instance=item)
    if form.is_valid():
        form.save()
        messages.success(request, "Metadados individuais salvos.")
    else:
        messages.error(request, "Revise os metadados obrigatórios do item.")
    return redirect("intake_module:item_detail", item_id=item.id)


def item_download(request, item_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    item = get_object_or_404(IntakeItem.objects.select_related("batch"), pk=item_id)
    try:
        download_drive_item(item)
        messages.success(request, "Arquivo baixado. O original foi preservado.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("intake_module:item_detail", item_id=item.id)


def item_clean(request, item_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    item = get_object_or_404(IntakeItem.objects.select_related("batch"), pk=item_id)
    try:
        clean_downloaded_item(item)
        messages.success(request, "Limpeza concluída e clean.txt gerado.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("intake_module:item_detail", item_id=item.id)


def batch_process_next(request, batch_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    batch = get_object_or_404(IntakeBatch, pk=batch_id)
    action = request.POST.get("action")
    try:
        if action == "download":
            item = batch.items.filter(status=IntakeState.DISCOVERED.value).order_by("order_index").first()
            if item is None:
                raise ValueError("Não há item DISCOVERED aguardando download.")
            download_drive_item(item)
            messages.success(request, f"Download sequencial concluído para {item.source_filename}.")
        elif action == "clean":
            item = batch.items.filter(status=IntakeState.DOWNLOADED.value).order_by("order_index").first()
            if item is None:
                raise ValueError("Não há item DOWNLOADED aguardando limpeza.")
            clean_downloaded_item(item)
            messages.success(request, f"Limpeza sequencial concluída para {item.source_filename}.")
        else:
            raise ValueError("Ação sequencial inválida.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("intake_module:batch_detail", batch_id=batch.id)


def item_prepare_codex(request, item_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    item = get_object_or_404(IntakeItem.objects.select_related("batch"), pk=item_id)
    form = PrepareCodexForm(request.POST)
    try:
        if not form.is_valid():
            raise ValueError("Informe o idioma de destino.")
        prepare_for_codex(item, target_language=form.cleaned_data["target_language"])
        messages.success(request, "Pacote preparado para o Codex.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("intake_module:item_detail", item_id=item.id)


def item_register_return(request, item_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    item = get_object_or_404(IntakeItem.objects.select_related("batch"), pk=item_id)
    form = TranslationReturnForm(request.POST, request.FILES)
    try:
        if not form.is_valid():
            raise ValueError("Envie o arquivo de retorno.")
        uploaded = form.cleaned_data["return_file"]
        register_translation_return(item, uploaded.name, b"".join(uploaded.chunks()))
        messages.success(request, "Retorno de tradução registrado.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("intake_module:item_detail", item_id=item.id)


def item_confirm_ready(request, item_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    item = get_object_or_404(IntakeItem.objects.select_related("batch"), pk=item_id)
    try:
        confirm_ready_for_editing(item)
        messages.success(request, "Item confirmado como READY_FOR_EDITING.")
    except Exception as exc:
        messages.error(request, str(exc))
    return redirect("intake_module:item_detail", item_id=item.id)


def item_handoff(request, item_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    item = get_object_or_404(IntakeItem.objects.select_related("batch"), pk=item_id)
    try:
        result = handoff_to_pipeline(item)
        messages.success(request, "Handoff concluído sem reexecutar Translate.")
        return redirect("edition_steps", edition_id=result.edition.id)
    except Exception as exc:
        messages.error(request, str(exc))
        return redirect("intake_module:item_detail", item_id=item.id)
