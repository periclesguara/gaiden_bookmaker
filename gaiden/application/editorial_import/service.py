from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from django.db import transaction
from django.utils import timezone

from pipeline.models import IncrementalEdition, IncrementalImportRun
from pipeline.services.incremental_export import export_changed_blocks
from pipeline.services.incremental_import import import_manifest

from .preview import preview_editorial_import
from .projection import project_bodies_after_blocks, project_catalog
from .validation import EditorialPackageValidationError


class StaleEditorialPreview(EditorialPackageValidationError):
    pass


def confirm_editorial_import(
    package_path: str | Path,
    manifest_path: str | Path,
    *,
    expected_package_sha256: str,
    expected_manifest_sha256: str,
    artifact_root: str | Path | None = None,
    blocks_directory: str | Path | None = None,
    import_attempt: int = 1,
    drive_destination: str | Path | None = None,
    failure_injector: Callable[[str], None] | None = None,
    publisher: Any = None,
) -> dict[str, Any]:
    """Revalidate and atomically persist the package and its current block batch."""
    plan = preview_editorial_import(
        package_path,
        manifest_path,
        artifact_root=artifact_root,
        blocks_directory=blocks_directory,
    )
    stale_errors = []
    if plan["package_sha256"] != expected_package_sha256:
        stale_errors.append("O pacote mudou depois da prévia.")
    if plan["manifest_sha256"] != expected_manifest_sha256:
        stale_errors.append("O manifesto mudou depois da prévia.")
    if stale_errors:
        raise StaleEditorialPreview(stale_errors)
    if not plan["can_confirm"]:
        raise EditorialPackageValidationError(["A prévia possui conflitos ou erros bloqueantes."] + plan["conflicts"])

    validated = plan["package"]
    with transaction.atomic():
        if failure_injector:
            failure_injector("before_catalog")
        catalog, editions = project_catalog(validated)
        if failure_injector:
            failure_injector("after_catalog")
        incremental = import_manifest(
            manifest_path,
            blocks_directory=blocks_directory,
            stop_on_conflict=True,
            import_attempt=import_attempt,
        )
        if incremental["conflicts"] or incremental["failed"]:
            raise EditorialPackageValidationError(["Falha durante a persistência incremental; o lote foi revertido."])
        if failure_injector:
            failure_injector("after_blocks")
        target_edition = editions[plan["incremental"].manifest["locale"]]
        incremental_edition = IncrementalEdition.objects.select_for_update().get(edition_id=incremental["edition_id"])
        if incremental_edition.editorial_edition_id != target_edition.id:
            incremental_edition.editorial_edition = target_edition
            incremental_edition.save(update_fields=["editorial_edition", "updated_at"])
        bodies = project_bodies_after_blocks(validated, editions, complete=incremental["next_sequence"] is None)
        if failure_injector:
            failure_injector("after_bodies")
        report = {
            "schema_version": 1,
            "package_sha256": plan["package_sha256"],
            "manifest_sha256": plan["manifest_sha256"],
            "status": "SUCCESS",
            "completed_at": timezone.now().isoformat(),
            "book_code": validated.package.book_code,
            "source_intake_item_id": validated.package.source_intake_item_id,
            "editorial_status": validated.package.status,
            "catalog": catalog,
            "blocks": incremental,
            "bodies": bodies,
            "warnings": plan["warnings"],
            "published": False,
            "build_executed": False,
        }
        run = IncrementalImportRun.objects.select_for_update().get(run_id=incremental["run_id"])
        run.result = {**run.result, "editorial_import": report}
        run.save(update_fields=["result"])

    drive_result = None
    drive_error = ""
    if drive_destination:
        try:
            drive_result = export_changed_blocks(
                incremental["edition_id"],
                drive_destination,
                publisher=publisher,
            )
        except (OSError, ValueError) as exc:
            drive_error = str(exc)
    return {**report, "drive": {"status": "FAILED" if drive_error else ("SUCCESS" if drive_result else "NOT_REQUESTED"), "result": drive_result, "error": drive_error}}
