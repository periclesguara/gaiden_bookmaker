from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from gaiden.chunker import make_chunks_from_text
from gaiden.domain.author_studio.enums import SplitStatus

logger = logging.getLogger(__name__)

DEFAULT_MIN_TOKENS = 400
DEFAULT_TARGET_TOKENS = 700
DEFAULT_MAX_TOKENS = 900


@dataclass(frozen=True)
class SplitResult:
    work_code: str
    chunk_count: int
    status: str


def _read_canonical(canonical) -> str:
    with canonical.text_file.open("r") as handle:
        return handle.read()


def split_work(*, work) -> SplitResult:
    from author_studio.models import CanonicalText, Work, WorkChunk, WorkSplit

    canonical = CanonicalText.objects.get(work=work)
    with transaction.atomic():
        locked_work = Work.objects.select_for_update().get(pk=work.pk)
        run, _ = WorkSplit.objects.update_or_create(
            work=locked_work,
            defaults={
                "canonical_text": canonical,
                "status": SplitStatus.RUNNING.value,
                "source_sha256": canonical.sha256,
                "chunk_count": 0,
                "error": "",
                "started_at": timezone.now(),
                "completed_at": None,
            },
        )

    try:
        text = _read_canonical(canonical)
        chunks = make_chunks_from_text(
            text,
            work.original_language or "en",
            DEFAULT_MIN_TOKENS,
            DEFAULT_TARGET_TOKENS,
            DEFAULT_MAX_TOKENS,
        )
        if not chunks:
            raise ValueError("O splitter não produziu chunks para o texto canônico.")

        with transaction.atomic():
            locked_work = Work.objects.select_for_update().get(pk=work.pk)
            WorkChunk.objects.filter(work=locked_work).delete()
            for sequence, chunk in enumerate(chunks, start=1):
                payload = chunk.text.strip() + "\n"
                record = WorkChunk(
                    work=locked_work,
                    canonical_text=canonical,
                    code=f"{locked_work.code}-CHK{sequence:04d}",
                    sequence=sequence,
                    unit_type=chunk.unit_type,
                    unit_title=chunk.unit_title,
                    sha256=chunk.sha256,
                    character_count=len(payload),
                    word_count=len(re.findall(r"\b\w+\b", payload, flags=re.UNICODE)),
                    estimated_tokens=chunk.est_tokens,
                )
                record.text_file.save("chunk.txt", ContentFile(payload.encode("utf-8")), save=False)
                record.save()
            run = WorkSplit.objects.select_for_update().get(work=locked_work)
            run.status = SplitStatus.COMPLETED.value
            run.source_sha256 = canonical.sha256
            run.chunk_count = len(chunks)
            run.error = ""
            run.completed_at = timezone.now()
            run.save(update_fields=["status", "source_sha256", "chunk_count", "error", "completed_at", "updated_at"])
    except Exception as exc:
        WorkSplit.objects.filter(work=work).update(
            status=SplitStatus.FAILED.value,
            error=str(exc)[:2000],
            completed_at=timezone.now(),
        )
        logger.exception("author_studio_split_failed work_id=%s code=%s", work.pk, work.code)
        raise

    logger.info("author_studio_split_completed work_id=%s code=%s chunks=%s", work.pk, work.code, len(chunks))
    return SplitResult(work.code, len(chunks), SplitStatus.COMPLETED.value)


def split_author_works(*, author) -> tuple[list[SplitResult], list[tuple[str, str]]]:
    results: list[SplitResult] = []
    errors: list[tuple[str, str]] = []
    works = author.works.select_related("canonical_text").order_by("id")
    for work in works:
        try:
            results.append(split_work(work=work))
        except Exception as exc:
            errors.append((work.code, str(exc)))
    return results, errors
