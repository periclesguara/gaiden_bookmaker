from __future__ import annotations

from django.contrib import messages
from django.core import signing
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render

from gaiden.application.editorial_import.preview import preview_editorial_import
from gaiden.application.editorial_import.service import confirm_editorial_import
from gaiden.application.editorial_import.validation import EditorialPackageValidationError
from gaiden.infrastructure.editorial_import_storage import stage_uploaded_package, validate_staging_path
from gaiden.infrastructure.drive_storage import DrivePathError, DriveStorageError, RcloneDriveStorage
from gaiden.application.intake.drive_import.service import (
    IntakeValidationError,
    StaleDrivePreview,
    confirm_drive_folder,
    preview_drive_folder,
    retry_drive_batch,
)
from pipeline.forms_incremental import (
    AutomatedEditorialConfirmForm,
    AutomatedEditorialPreviewForm,
    DriveFolderConfirmForm,
    DriveFolderPreviewForm,
    DriveFolderRetryForm,
    IncrementalImportForm,
)
from pipeline.models import IncrementalEdition, IntakeBatch
from pipeline.services.incremental_export import export_changed_blocks
from pipeline.services.incremental_import import (
    ImportRunConflict,
    ManifestValidationError,
    import_manifest,
    preview_manifest,
)


PREVIEW_TOKEN_SALT = "gaiden.automated-editorial-import.v1"
PREVIEW_TOKEN_MAX_AGE = 60 * 60
DRIVE_PREVIEW_TOKEN_SALT = "gaiden.automated-drive-folder.v1"


def automated_editorial_import_dashboard(request: HttpRequest) -> HttpResponse:
    if request.method != "GET":
        return redirect("automated_editorial_import")
    return _render_automated(request)


def automated_drive_browse(request: HttpRequest) -> JsonResponse:
    if request.method != "GET":
        return JsonResponse({"error": "Método não permitido."}, status=405)
    folder = (request.GET.get("folder") or "").strip()
    try:
        storage = RcloneDriveStorage()
        return JsonResponse({"root": storage.inbox, "folders": storage.list_folders(folder)})
    except (DrivePathError, DriveStorageError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)


def automated_drive_folder_preview(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect("automated_editorial_import")
    form = DriveFolderPreviewForm(request.POST)
    if not form.is_valid():
        return _render_automated(request, drive_preview_form=form)
    try:
        storage = RcloneDriveStorage()
        values = form.cleaned_data
        preview = preview_drive_folder(
            storage,
            folder=values["folder_path"],
            recursive=bool(values["recursive"]),
            batch_name=values["batch_name"],
            default_author=values.get("default_author") or "",
            source_language=values["source_language"],
            target_language=values.get("target_language") or "",
            seal=values.get("seal") or "",
        )
        token = signing.dumps(preview, salt=DRIVE_PREVIEW_TOKEN_SALT, compress=True)
        return _render_automated(
            request,
            drive_preview_form=form,
            drive_preview=preview,
            drive_confirm_form=DriveFolderConfirmForm(initial={"preview_token": token}),
        )
    except (DrivePathError, DriveStorageError, IntakeValidationError, ValueError) as exc:
        messages.error(request, str(exc))
        return _render_automated(request, drive_preview_form=form)


def automated_drive_folder_confirm(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect("automated_editorial_import")
    form = DriveFolderConfirmForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Confirmação inválida; gere uma nova prévia.")
        return _render_automated(request)
    try:
        preview = signing.loads(
            form.cleaned_data["preview_token"],
            salt=DRIVE_PREVIEW_TOKEN_SALT,
            max_age=PREVIEW_TOKEN_MAX_AGE,
        )
        result = confirm_drive_folder(
            RcloneDriveStorage(),
            preview,
            selected_paths=request.POST.getlist("selected_paths"),
        )
        if result["status"] == "REGISTERED":
            messages.success(request, "Pasta do Drive importada e promovida com verificação SHA-256.")
        else:
            messages.warning(request, "A importação foi registrada com itens pendentes ou em conflito; use o retry do mesmo lote.")
        return _render_automated(request, drive_import_result=result)
    except (signing.BadSignature, signing.SignatureExpired, DrivePathError, DriveStorageError, IntakeValidationError, StaleDrivePreview, ValueError) as exc:
        messages.error(request, str(exc))
        return _render_automated(request)


def automated_drive_import_retry(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect("automated_editorial_import")
    form = DriveFolderRetryForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Lote inválido para retry.")
        return _render_automated(request)
    try:
        result = retry_drive_batch(RcloneDriveStorage(), form.cleaned_data["batch_code"])
        messages.success(request, f"Retry concluído para {result['batch_code']}.")
        return _render_automated(request, drive_import_result=result)
    except (IntakeBatch.DoesNotExist, DrivePathError, DriveStorageError, IntakeValidationError, ValueError) as exc:
        messages.error(request, str(exc))
        return _render_automated(request)


def automated_editorial_import_preview(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect("automated_editorial_import")
    form = AutomatedEditorialPreviewForm(request.POST, request.FILES)
    if not form.is_valid():
        return _render_automated(request, preview_form=form)
    try:
        staged = stage_uploaded_package(
            form.cleaned_data["package_file"],
            form.cleaned_data["manifest_file"],
            form.cleaned_data["artifact_files"],
        )
        preview = preview_editorial_import(
            staged["package"],
            staged["manifest"],
            artifact_root=staged["root"],
            blocks_directory=staged["blocks"],
        )
        token = signing.dumps(
            {
                "root": str(staged["root"]),
                "package": str(staged["package"]),
                "manifest": str(staged["manifest"]),
                "package_sha256": preview["package_sha256"],
                "manifest_sha256": preview["manifest_sha256"],
            },
            salt=PREVIEW_TOKEN_SALT,
            compress=True,
        )
        confirm_form = AutomatedEditorialConfirmForm(
            initial={
                "preview_token": token,
                "drive_destination": form.cleaned_data.get("drive_destination") or "",
            }
        )
        return _render_automated(request, preview_form=form, preview=preview, confirm_form=confirm_form)
    except (EditorialPackageValidationError, ManifestValidationError, OSError, ValueError) as exc:
        messages.error(request, str(exc))
        return _render_automated(request, preview_form=form)


def automated_editorial_import_confirm(request: HttpRequest) -> HttpResponse:
    if request.method != "POST":
        return redirect("automated_editorial_import")
    form = AutomatedEditorialConfirmForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Confirmação inválida; gere uma nova prévia.")
        return _render_automated(request)
    try:
        token = signing.loads(
            form.cleaned_data["preview_token"],
            salt=PREVIEW_TOKEN_SALT,
            max_age=PREVIEW_TOKEN_MAX_AGE,
        )
        root = validate_staging_path(token["root"])
        package_path = validate_staging_path(token["package"])
        manifest_path = validate_staging_path(token["manifest"])
        result = confirm_editorial_import(
            package_path,
            manifest_path,
            expected_package_sha256=token["package_sha256"],
            expected_manifest_sha256=token["manifest_sha256"],
            artifact_root=root,
            blocks_directory=root,
            drive_destination=form.cleaned_data.get("drive_destination") or None,
        )
        if result["drive"]["status"] == "FAILED":
            messages.warning(request, "A importação foi confirmada, mas o reenvio ao Drive falhou e não foi marcado como concluído.")
        else:
            messages.success(request, "Pacote editorial importado com confirmação transacional.")
        return _render_automated(request, import_result=result)
    except (signing.BadSignature, signing.SignatureExpired, KeyError, EditorialPackageValidationError, ManifestValidationError, ImportRunConflict, OSError, ValueError) as exc:
        messages.error(request, str(exc))
        return _render_automated(request)


def _render_automated(
    request: HttpRequest,
    *,
    preview_form: AutomatedEditorialPreviewForm | None = None,
    preview: dict | None = None,
    confirm_form: AutomatedEditorialConfirmForm | None = None,
    import_result: dict | None = None,
    drive_preview_form: DriveFolderPreviewForm | None = None,
    drive_preview: dict | None = None,
    drive_confirm_form: DriveFolderConfirmForm | None = None,
    drive_import_result: dict | None = None,
) -> HttpResponse:
    drive_folders = []
    drive_browse_error = ""
    drive_location = "Google Drive · 01_INBOX_RAW"
    try:
        drive_storage = RcloneDriveStorage()
        drive_folders = drive_storage.list_folders()
        drive_location = f"{drive_storage.remote}:{drive_storage.inbox}"
    except (DrivePathError, DriveStorageError, ValueError) as exc:
        drive_browse_error = str(exc)
    return render(
        request,
        "pipeline/automated_editorial_import.html",
        {
            "preview_form": preview_form or AutomatedEditorialPreviewForm(),
            "preview": preview,
            "confirm_form": confirm_form,
            "import_result": import_result,
            "incremental_editions": IncrementalEdition.objects.order_by("edition_id"),
            "drive_preview_form": drive_preview_form or DriveFolderPreviewForm(),
            "drive_preview": drive_preview,
            "drive_confirm_form": drive_confirm_form,
            "drive_import_result": drive_import_result,
            "drive_folders": drive_folders,
            "drive_location": drive_location,
            "drive_browse_error": drive_browse_error,
            "intake_batches": IntakeBatch.objects.prefetch_related("items").order_by("-created_at")[:20],
        },
    )


def incremental_import_dashboard(request: HttpRequest) -> HttpResponse:
    form = IncrementalImportForm(request.POST or None)
    preview = None
    import_result = None
    export_result = None
    action = (request.POST.get("action") or "preview").strip() if request.method == "POST" else ""

    if request.method == "POST" and form.is_valid():
        values = form.cleaned_data
        try:
            preview = preview_manifest(
                values["manifest_path"],
                blocks_directory=values.get("blocks_directory") or None,
            )
            if action == "import":
                if not preview.can_import:
                    messages.error(request, "O lote possui erros de arquivo e não pode ser confirmado.")
                else:
                    import_result = import_manifest(
                        values["manifest_path"],
                        blocks_directory=values.get("blocks_directory") or None,
                        stop_on_conflict=bool(values.get("stop_on_conflict")),
                        import_attempt=values["import_attempt"],
                    )
                    messages.success(
                        request,
                        "Lote importado e ponto de retomada persistido.",
                    )
                    destination = (values.get("drive_destination") or "").strip()
                    if destination:
                        export_result = export_changed_blocks(
                            import_result["edition_id"],
                            destination,
                        )
                        messages.success(
                            request,
                            f"Reenvio concluído: {len(export_result['exported_sequences'])} bloco(s) alterado(s).",
                        )
        except (ManifestValidationError, ImportRunConflict, OSError, ValueError) as exc:
            messages.error(request, str(exc))

    editions = IncrementalEdition.objects.order_by("edition_id")
    return render(
        request,
        "pipeline/incremental_import.html",
        {
            "form": form,
            "preview": preview,
            "import_result": import_result,
            "export_result": export_result,
            "incremental_editions": editions,
        },
    )
