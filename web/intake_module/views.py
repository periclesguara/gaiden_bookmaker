from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from gaiden.application.intake import (
    clean_downloaded_item,
    confirm_ready_for_editing,
    discover_drive_folder,
    download_drive_item,
    handoff_to_pipeline,
    open_in_bookmaker,
    prepare_for_codex,
    reconcile_batch_downloads,
    reconcile_item_download,
    register_translation_return,
    store_uploaded_files,
)
from gaiden.domain.intake import IntakeState
from gaiden.infrastructure.intake_drive import RcloneClient

from .forms import (
    IntakeBatchForm,
    IntakeItemMetadataForm,
    IntakeUploadForm,
    LocalDirectoryForm,
    PrepareCodexForm,
    TranslationReturnForm,
)
from .models import IntakeBatch, IntakeItem


DRIVE_LOOKUP_ERROR = (
    "Não foi possível consultar o Google Drive. "
    "Verifique a configuração do remote gaiden_drive."
)
SELECTED_DRIVE_FOLDER_SESSION_KEY = "intake_selected_drive_folder"


def batch_list(request):
    return render(request, "intake_module/batch_list.html", {"batches": IntakeBatch.objects.all()})


def _validated_folder_name(value: str) -> str:
    name = (value or "").strip()
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or ":" in name
        or name.startswith("~")
    ):
        raise ValueError("Seleção de pasta inválida; caminhos arbitrários não são aceitos.")
    return name


def _drive_client_context():
    try:
        client = RcloneClient()
        return (
            client,
            client.remote,
            getattr(client, "inbox", "01_INBOX_RAW"),
            client.executable_available,
            "",
        )
    except Exception:
        return None, "gaiden_drive:", "01_INBOX_RAW", False, DRIVE_LOOKUP_ERROR


def _available_drive_folders(client) -> list[str]:
    client.check_available()
    return [_validated_folder_name(folder) for folder in client.list_folders("")]


def drive_folders(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    client, remote_name, inbox_name, rclone_available, configuration_error = _drive_client_context()
    folders = []
    try:
        if client is None:
            raise RuntimeError("Invalid rclone configuration")
        folders = _available_drive_folders(client)
        messages.success(request, f"{len(folders)} pastas encontradas; nenhum arquivo foi baixado.")
    except Exception:
        messages.error(request, DRIVE_LOOKUP_ERROR)
    return render(
        request,
        "intake_module/drive_folders.html",
        {
            "drive_folders": folders,
            "remote_name": remote_name,
            "inbox_name": inbox_name,
            "rclone_available": rclone_available,
            "configuration_error": configuration_error,
        },
    )


def drive_folder_select(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        selected_folder = _validated_folder_name(request.POST.get("drive_folder", ""))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("intake_module:drive_folders")
    client, _remote_name, _inbox_name, _rclone_available, _configuration_error = _drive_client_context()
    try:
        if client is None:
            raise RuntimeError("Invalid rclone configuration")
        if selected_folder not in _available_drive_folders(client):
            messages.error(request, "A pasta selecionada não existe em gaiden_drive:01_INBOX_RAW.")
            return redirect("intake_module:drive_folders")
    except Exception:
        messages.error(request, DRIVE_LOOKUP_ERROR)
        return redirect("intake_module:drive_folders")
    request.session[SELECTED_DRIVE_FOLDER_SESSION_KEY] = selected_folder
    return redirect("intake_module:batch_create")


def batch_create(request):
    action = request.POST.get("create_action") if request.method == "POST" else ""
    data = request.POST.copy() if request.method == "POST" else None
    selected_drive_folder = request.session.get(SELECTED_DRIVE_FOLDER_SESSION_KEY, "")
    client, remote_name, inbox_name, rclone_available, configuration_error = _drive_client_context()

    if action in {"select_drive_folder", "save_drive_folder"}:
        try:
            if client is None:
                raise RuntimeError("Invalid rclone configuration")
            selected_folder = _validated_folder_name(
                request.POST.get("drive_folder", "") or selected_drive_folder
            )
            selected_drive_folder = selected_folder
            if selected_folder not in _available_drive_folders(client):
                raise ValueError("A pasta selecionada não pertence a gaiden_drive:01_INBOX_RAW.")
            data["name"] = selected_folder
            form = IntakeBatchForm(data)
            local_form = LocalDirectoryForm(request.POST, request.FILES)
            if form.is_valid():
                batch = form.save(commit=False)
                batch.drive_relative_path = client.stored_folder_path(selected_folder)
                batch.save()
                report = discover_drive_folder(batch, batch.drive_relative_path, client=client)
                request.session[_report_key(batch.id)] = report
                request.session.pop(SELECTED_DRIVE_FOLDER_SESSION_KEY, None)
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
                return redirect("intake_module:batch_files", batch_id=batch.id)
        except ValueError as exc:
            messages.error(request, str(exc))
            form = IntakeBatchForm(data)
            local_form = LocalDirectoryForm(request.POST, request.FILES)
        except Exception:
            messages.error(request, DRIVE_LOOKUP_ERROR)
            form = IntakeBatchForm(data)
            local_form = LocalDirectoryForm(request.POST, request.FILES)
    elif action == "select_local_folder":
        try:
            local_folder_name = _validated_folder_name(request.POST.get("local_folder_name", ""))
            data["name"] = local_folder_name
            form = IntakeBatchForm(data)
            local_form = LocalDirectoryForm(request.POST, request.FILES)
            if not local_form.is_valid() or not request.FILES.getlist("folder_files"):
                raise ValueError("Selecione uma pasta local com arquivos compatíveis.")
            if form.is_valid():
                batch = form.save()
                uploads = request.FILES.getlist("folder_files")
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
                return redirect("intake_module:batch_files", batch_id=batch.id)
        except Exception as exc:
            messages.error(request, str(exc))
            form = IntakeBatchForm(data)
            local_form = LocalDirectoryForm(request.POST, request.FILES)
    else:
        initial = {"name": selected_drive_folder} if selected_drive_folder else None
        form = IntakeBatchForm(data, initial=initial)
        local_form = LocalDirectoryForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            batch = form.save()
            if "save_only" in request.POST:
                return redirect("intake_module:batch_detail", batch_id=batch.id)
            return redirect("intake_module:batch_files", batch_id=batch.id)

    return render(
        request,
        "intake_module/batch_form.html",
        {
            "form": form,
            "local_form": local_form,
            "selected_drive_folder": selected_drive_folder,
            "remote_name": remote_name,
            "inbox_name": inbox_name,
            "rclone_available": rclone_available,
            "configuration_error": configuration_error,
        },
    )


def batch_detail(request, batch_id: int):
    batch = get_object_or_404(IntakeBatch, pk=batch_id)
    items = batch.items.select_related("duplicate_of").all()
    reconciliation_preview = reconcile_batch_downloads(batch, dry_run=True)
    reconcilable_item_ids = {
        row["item_id"]
        for row in reconciliation_preview["adoptable"]
        if row["from_status"] == IntakeState.FAILED.value
    }
    return render(
        request,
        "intake_module/batch_detail.html",
        {
            "batch": batch,
            "items": items,
            "upload_form": IntakeUploadForm(),
            "can_download_next": items.filter(status=IntakeState.DISCOVERED.value).exists(),
            "can_clean_next": items.filter(
                status=IntakeState.DOWNLOADED.value,
                duplicate_of__isnull=True,
            ).exists(),
            "show_reconciliation": bool(
                reconciliation_preview["adoptable"]
                or reconciliation_preview["interrupted"]
            ),
            "reconcilable_item_ids": reconcilable_item_ids,
            "bookmaker_item_ids": {
                item.id for item in items if _can_open_in_bookmaker(item)
            },
            "file_summary": request.session.get(_summary_key(batch.id), _empty_summary()),
        },
    )


def batch_reconcile(request, batch_id: int):
    batch = get_object_or_404(IntakeBatch, pk=batch_id)
    if request.method == "GET":
        report = reconcile_batch_downloads(batch, dry_run=True)
        return render(
            request,
            "intake_module/batch_reconcile.html",
            {"batch": batch, "report": report},
        )
    if request.method == "POST" and request.POST.get("confirm") == "1":
        report = reconcile_batch_downloads(batch, dry_run=False)
        messages.success(
            request,
            "Reconciliação concluída: "
            f"{len(report['adoptable'])} adotados, "
            f"{len(report['interrupted'])} interrompidos e "
            f"{len(report['conflicts'])} conflitos.",
        )
        return redirect("intake_module:batch_detail", batch_id=batch.id)
    return HttpResponseNotAllowed(["GET", "POST"])


def item_detail(request, item_id: int):
    item = get_object_or_404(
        IntakeItem.objects.select_related("batch", "duplicate_of"),
        pk=item_id,
    )
    reconciliation_preview = reconcile_item_download(item, dry_run=True)
    return render(
        request,
        "intake_module/item_detail.html",
        {
            "item": item,
            "metadata_form": IntakeItemMetadataForm(instance=item),
            "prepare_form": PrepareCodexForm(initial={"target_language": item.target_language}),
            "return_form": TranslationReturnForm(),
            "can_reconcile_item": bool(
                item.status == IntakeState.FAILED.value
                and reconciliation_preview["adoptable"]
            ),
            "can_open_in_bookmaker": _can_open_in_bookmaker(item),
        },
    )


def _can_open_in_bookmaker(item: IntakeItem) -> bool:
    return bool(
        item.status == IntakeState.DOWNLOADED.value
        and not item.duplicate_of_id
        and item.batch.author_default
        and item.confirmed_title
        and item.original_year
        and item.book_code
        and item.batch.source_language
        and item.target_language
        and item.original_path
        and item.source_sha256
    )


def item_reconcile(request, item_id: int):
    item = get_object_or_404(IntakeItem.objects.select_related("batch"), pk=item_id)
    if request.method == "GET":
        report = reconcile_item_download(item, dry_run=True)
        return render(
            request,
            "intake_module/batch_reconcile.html",
            {"batch": item.batch, "item": item, "report": report},
        )
    if request.method == "POST" and request.POST.get("confirm") == "1":
        report = reconcile_item_download(item, dry_run=False)
        if report["adoptable"]:
            messages.success(
                request,
                f"{item.source_filename} reconciliado com sucesso; "
                "o arquivo existente foi adotado sem novo download.",
            )
        else:
            messages.success(request, "Item já reconciliado; nenhuma alteração necessária.")
        return redirect("intake_module:batch_detail", batch_id=item.batch_id)
    return HttpResponseNotAllowed(["GET", "POST"])


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
    report = request.session.get(_report_key(batch.id))
    client, remote_name, inbox_name, rclone_available, configuration_error = _drive_client_context()
    if request.method == "POST":
        try:
            if client is None:
                raise ValueError(configuration_error or "Configuração do rclone inválida")
            if not batch.drive_relative_path:
                raise ValueError("Nenhuma pasta do Google Drive foi selecionada para este lote.")
            action = request.POST.get("drive_action")
            if action == "verify":
                selected_folder = client.direct_child_name(batch.drive_relative_path)
                folders = _available_drive_folders(client)
                if selected_folder not in folders:
                    raise ValueError("A pasta armazenada não existe em gaiden_drive:01_INBOX_RAW.")
                messages.success(request, "Pasta verificada com sucesso; nenhum arquivo foi transferido.")
            elif action == "list":
                report = discover_drive_folder(
                    batch,
                    batch.drive_relative_path,
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
    return render(
        request,
        "intake_module/batch_files.html",
        {
            "batch": batch,
            "upload_form": IntakeUploadForm(),
            "report": report,
            "remote_name": remote_name,
            "inbox_name": inbox_name,
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
            item = (
                batch.items.filter(
                    status=IntakeState.DOWNLOADED.value,
                    duplicate_of__isnull=True,
                )
                .order_by("order_index")
                .first()
            )
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


def item_open_bookmaker(request, item_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    item = get_object_or_404(IntakeItem.objects.select_related("batch"), pk=item_id)
    try:
        result = open_in_bookmaker(item)
        messages.success(request, "Livro disponível no Gaiden Bookmaker.")
        return redirect("edition_steps", edition_id=result.edition.id)
    except Exception as exc:
        messages.error(request, str(exc))
        return redirect("intake_module:item_detail", item_id=item.id)
