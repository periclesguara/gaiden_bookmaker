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
)
from gaiden.domain.intake import IntakeState

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
        return redirect("intake_module:batch_detail", batch_id=batch.id)
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
        results = ingest_many(batch, request.FILES.getlist("files"))
        imported = sum(not result.get("ignored", False) for result in results)
        ignored = len(results) - imported
        messages.success(request, f"Importados: {imported}; ignorados: {ignored}.")
    else:
        messages.error(request, "Selecione um ou mais arquivos válidos.")
    return redirect("intake_module:batch_detail", batch_id=batch.id)


def batch_drive(request, batch_id: int):
    batch = get_object_or_404(IntakeBatch, pk=batch_id)
    form = DriveSyncForm(request.POST or None, initial={"relative_folder": batch.drive_relative_path})
    report = None
    if request.method == "POST" and form.is_valid():
        try:
            batch.drive_relative_path = form.cleaned_data["relative_folder"]
            batch.save(update_fields=["drive_relative_path", "updated_at"])
            report = discover_drive_folder(batch, form.cleaned_data["relative_folder"])
            messages.success(
                request,
                f"Drive: {len(report['discovered'])} descobertos, {len(report['existing'])} já cadastrados, "
                f"{len(report['ignored'])} ignorados, "
                f"{len(report['errors'])} erros.",
            )
        except Exception as exc:
            messages.error(request, str(exc))
    return render(request, "intake_module/drive_sync.html", {"batch": batch, "form": form, "report": report})


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
