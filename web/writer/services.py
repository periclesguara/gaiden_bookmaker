import hashlib

from django.core.files.base import ContentFile
from django.db import transaction

from author_studio.models import CanonicalText
from editorial.models import EditionBuild
from gaiden.application.builds.finalized_projects import mark_build_outdated
from gaiden.domain.author_studio.enums import CanonicalTextStatus

from .models import (
    EditorialWorkLink,
    Manuscript,
    ManuscriptVersion,
    WriterPromotionEvent,
    WriterStatus,
)


class WriterIdentityError(ValueError):
    pass


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


def resolve_editorial_work(manuscript: Manuscript):
    try:
        link = (
            EditorialWorkLink.objects.select_for_update()
            .select_related("editorial_work", "author_work")
            .get(author_work=manuscript.work)
        )
    except EditorialWorkLink.DoesNotExist as exc:
        raise WriterIdentityError(
            "Writer promotion is blocked: this Author Studio work has no explicit editorial identity link."
        ) from exc
    if link.author_work_id != manuscript.work_id:
        raise WriterIdentityError("Writer promotion is blocked by an inconsistent editorial identity link.")
    return link.editorial_work


def promote_version(
    version: ManuscriptVersion,
    *,
    editor_approval: str,
    reason: str,
    actor: str = "system",
) -> WriterPromotionEvent:
    if not editor_approval.strip() or not reason.strip():
        raise ValueError("Editor approval and a promotion reason are required.")
    created_file_name = ""
    storage_backend = None
    try:
        with transaction.atomic():
            version = (
                ManuscriptVersion.objects.select_for_update()
                .select_related("manuscript__work")
                .get(pk=version.pk)
            )
            editorial_work = resolve_editorial_work(version.manuscript)
            previous = CanonicalText.objects.select_for_update().filter(work=version.manuscript.work).first()
            if previous is None:
                raise ValueError("The work must have an ingested canonical source before Writer promotion.")
            existing_event = WriterPromotionEvent.objects.filter(
                manuscript=version.manuscript, promoted_sha256=version.sha256
            ).first()
            if previous.sha256 == version.sha256 and existing_event:
                return existing_event

            old_sha = previous.sha256
            old_path = previous.text_file.name
            storage_backend = previous.text_file.storage
            previous.text_file.save(
                f"writer-v{version.version}.txt",
                ContentFile(version.content.encode("utf-8")),
                save=False,
            )
            created_file_name = previous.text_file.name
            previous.sha256 = version.sha256
            previous.character_count = len(version.content)
            previous.word_count = len(version.content.split())
            previous.status = CanonicalTextStatus.READY.value
            previous.save()

            for build in EditionBuild.objects.select_for_update().filter(
                edition__work=editorial_work,
                status=EditionBuild.STATUS_DONE,
                is_final=True,
            ):
                mark_build_outdated(
                    build.id,
                    actor=actor,
                    reason=f"Writer canonical promotion V{version.version}: {reason.strip()}",
                )

            version.manuscript.status = WriterStatus.PROMOTED
            version.manuscript.save(update_fields=["status", "updated_at"])
            return WriterPromotionEvent.objects.create(
                manuscript=version.manuscript,
                version=version,
                editor_approval=editor_approval.strip(),
                actor=actor,
                reason=reason.strip(),
                promoted_sha256=version.sha256,
                previous_canonical_sha256=old_sha,
                previous_canonical_path=old_path,
                new_canonical_path=created_file_name,
            )
    except Exception:
        if created_file_name and storage_backend is not None:
            storage_backend.delete(created_file_name)
        raise
