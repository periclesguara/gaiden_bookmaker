from __future__ import annotations

from django.db import transaction

from ..models import Chapter
from .google_docs import _credentials


def import_chapter_output(chapter: Chapter) -> Chapter:
    if chapter.status != Chapter.Status.WAITING_FOR_OUTPUT:
        raise ValueError("chapter must be waiting for external output")
    if not chapter.drive_output_id:
        raise ValueError("output Google Doc is not registered")
    from googleapiclient.discovery import build

    docs = build("docs", "v1", credentials=_credentials(), cache_discovery=False)
    payload = docs.documents().get(documentId=chapter.drive_output_id).execute()
    text = "\n".join(
        "".join(item.get("textRun", {}).get("content", "") for item in element.get("paragraph", {}).get("elements", []))
        for element in payload.get("body", {}).get("content", []) if "paragraph" in element
    ).strip()
    if not text:
        raise ValueError("output Google Doc is empty")
    with transaction.atomic():
        current = Chapter.objects.select_for_update().get(pk=chapter.pk)
        current.generated_text = text
        current.status = current.Status.OUTPUT_IMPORTED
        current.error_message = ""
        current.save(update_fields=("generated_text", "status", "error_message", "updated_at"))
        return current
