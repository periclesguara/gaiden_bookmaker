from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.utils.text import slugify

from gaiden.domain.author_studio.codes import canonicalize
from gaiden.domain.author_studio.exceptions import DuplicateWorkError

logger = logging.getLogger(__name__)


def update_work(*, work, title: str, original_language: str = ""):
    """Update editable metadata while preserving the editorial code."""
    from author_studio.models import Work

    display_title = " ".join((title or "").split())
    canonical_title = canonicalize(display_title)
    if not canonical_title:
        raise ValueError("Informe um título válido.")

    with transaction.atomic():
        locked = Work.objects.select_for_update().get(pk=work.pk)
        duplicate = Work.objects.filter(
            author=locked.author,
            canonical_title=canonical_title,
        ).exclude(pk=locked.pk)
        if duplicate.exists():
            raise DuplicateWorkError("Esta obra já está cadastrada para o autor.")

        slug_base = slugify(display_title) or locked.code.lower()
        slug = slug_base
        sequence = 2
        while Work.objects.filter(author=locked.author, slug=slug).exclude(pk=locked.pk).exists():
            slug = f"{slug_base}-{sequence}"
            sequence += 1

        original_code = locked.code
        locked.title = display_title
        locked.canonical_title = canonical_title
        locked.slug = slug
        locked.original_language = (original_language or "").strip()
        try:
            locked.save(update_fields=["title", "canonical_title", "slug", "original_language", "updated_at"])
        except IntegrityError as exc:
            raise DuplicateWorkError("Esta obra já está cadastrada para o autor.") from exc

    logger.info("work_updated id=%s code=%s", locked.pk, original_code)
    return locked
