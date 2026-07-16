from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from django.apps import apps

from gaiden.infrastructure import storage
from gaiden.infrastructure.intake_drive import RcloneClient


class HeadingCleanerDriveError(RuntimeError):
    pass


@dataclass(frozen=True)
class HeadingCleanerDriveDestination:
    item_id: int
    folder: str
    remote_filename: str


@dataclass(frozen=True)
class HeadingCleanerDriveResult:
    source_path: Path
    remote_path: str
    no_op: bool


def resolve_heading_cleaner_destination(edition) -> HeadingCleanerDriveDestination | None:
    IntakeItem = apps.get_model("intake_module", "IntakeItem")
    item = (
        IntakeItem.objects.select_related("batch")
        .filter(handoff_edition_id=edition.id, duplicate_of__isnull=True)
        .order_by("id")
        .first()
    )
    if item is None or not item.batch.drive_relative_path:
        return None
    source_stem = Path(item.source_filename).stem
    return HeadingCleanerDriveDestination(
        item_id=item.id,
        folder=item.batch.drive_relative_path,
        remote_filename=f"{source_stem}_heading_clean.txt",
    )


def send_heading_cleaner_to_drive(edition, *, client=None) -> HeadingCleanerDriveResult:
    destination = resolve_heading_cleaner_destination(edition)
    if destination is None:
        raise HeadingCleanerDriveError("This edition is not linked to an Intake Drive folder")
    source_path = storage.heading_cleaner_dir(edition.work.code) / "clean.txt"
    if source_path.is_symlink() or not source_path.is_file():
        raise HeadingCleanerDriveError("HeadingCleaner output is not available")

    drive_client = client or RcloneClient()
    drive_client.check_available()
    folder_name = drive_client.direct_child_name(destination.folder)
    if folder_name not in drive_client.list_folders(""):
        raise HeadingCleanerDriveError("The linked Intake Drive folder no longer exists")
    result = drive_client.upload_file(
        source_path,
        destination.folder,
        destination.remote_filename,
    )
    return HeadingCleanerDriveResult(
        source_path=source_path,
        remote_path=result["remote_path"],
        no_op=bool(result["no_op"]),
    )
