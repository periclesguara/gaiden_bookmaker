import hashlib
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import transaction

from editorial.models import EditionBuild, EditionPipeline, Work as EditorialWork
from author_studio.models import CanonicalText

from .models import Manuscript, ManuscriptVersion, WriterPromotionEvent, WriterStatus


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@transaction.atomic
def create_version(manuscript: Manuscript, *, content: str, change_note: str = "") -> ManuscriptVersion:
    locked = Manuscript.objects.select_for_update().get(pk=manuscript.pk)
    latest = locked.versions.order_by("-version").first()
    return ManuscriptVersion.objects.create(
        manuscript=locked,
        version=(latest.version if latest else 0) + 1,
        content=content,
        sha256=content_sha256(content),
        change_note=change_note,
    )


@transaction.atomic
def promote_version(version: ManuscriptVersion, *, editor_approval: str) -> WriterPromotionEvent:
    version = ManuscriptVersion.objects.select_for_update().select_related("manuscript__work").get(pk=version.pk)
    previous = CanonicalText.objects.select_for_update().filter(work=version.manuscript.work).first()
    if previous and previous.sha256 == version.sha256:
        event = WriterPromotionEvent.objects.filter(
            manuscript=version.manuscript, promoted_sha256=version.sha256
        ).first()
        if event:
            return event
    if previous is None:
        raise ValueError("The work must have an ingested canonical source before Writer promotion.")
    old_sha = previous.sha256
    old_path = previous.text_file.name
    previous.text_file.save(
        f"writer-v{version.version}.txt", ContentFile(version.content.encode("utf-8")), save=False
    )
    previous.sha256 = version.sha256
    previous.character_count = len(version.content)
    previous.word_count = len(version.content.split())
    previous.status = "APPROVED"
    previous.save()
    version.manuscript.status = WriterStatus.PROMOTED
    version.manuscript.save(update_fields=["status", "updated_at"])
    event = WriterPromotionEvent.objects.create(
        manuscript=version.manuscript,
        version=version,
        editor_approval=editor_approval,
        promoted_sha256=version.sha256,
        previous_canonical_sha256=old_sha,
        previous_canonical_path=old_path,
    )
    editorial_work = EditorialWork.objects.filter(code=version.manuscript.work.code).first()
    if editorial_work:
        EditionPipeline.objects.filter(edition__work=editorial_work).update(build_outdated=True, editorial_changed=True)
        EditionBuild.objects.filter(edition__work=editorial_work, status=EditionBuild.STATUS_DONE).update(
            status=EditionBuild.STATUS_OUTDATED, is_final=False
        )
    return event
