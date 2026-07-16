from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from gaiden.application.intake import (
    confirm_ready_for_editing,
    ingest_many,
    prepare_for_codex,
    register_translation_return,
    synchronize_drive_folder,
)

from .forms import DriveSyncForm, IntakeBatchForm, IntakeUploadForm, PrepareCodexForm, TranslationReturnForm
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
    return render(
        request,
        "intake_module/batch_detail.html",
        {"batch": batch, "items": batch.items.all(), "upload_form": IntakeUploadForm()},
    )


def item_detail(request, item_id: int):
    item = get_object_or_404(IntakeItem.objects.select_related("batch"), pk=item_id)
    return render(
        request,
        "intake_module/item_detail.html",
        {
            "item": item,
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
            report = synchronize_drive_folder(batch, form.cleaned_data["relative_folder"])
            batch.drive_relative_path = form.cleaned_data["relative_folder"]
            batch.save(update_fields=["drive_relative_path", "updated_at"])
            messages.success(
                request,
                f"Drive: {len(report['imported'])} importados, {len(report['ignored'])} ignorados, "
                f"{len(report['errors'])} erros.",
            )
        except Exception as exc:
            messages.error(request, str(exc))
    return render(request, "intake_module/drive_sync.html", {"batch": batch, "form": form, "report": report})


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
