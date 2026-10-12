from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.utils.text import slugify

from gaiden.domain.author_studio.codes import canonicalize, generate_work_code
from gaiden.domain.author_studio.enums import WorkStatus
from gaiden.domain.author_studio.exceptions import DuplicateWorkError

logger = logging.getLogger(__name__)


def create_work(*, author, title: str, original_language: str = ""):
    from author_studio.models import Work

    display_title = " ".join((title or "").split())
    canonical_title = canonicalize(display_title)
    if not canonical_title:
        raise ValueError("Informe um título válido.")
    with transaction.atomic():
        locked_author = type(author).objects.select_for_update().get(pk=author.pk)
        if Work.objects.filter(author=locked_author, canonical_title=canonical_title).exists():
            raise DuplicateWorkError("Esta obra já está cadastrada para o autor.")
        code = generate_work_code(
            locked_author.code,
            display_title,
            lambda candidate: Work.objects.filter(code=candidate).exists(),
        )
        slug_base = slugify(display_title) or code.lower()
        slug = slug_base
        number = 2
        while Work.objects.filter(author=locked_author, slug=slug).exists():
            slug = f"{slug_base}-{number}"
            number += 1
        try:
            work = Work.objects.create(
                author=locked_author,
                title=display_title,
                canonical_title=canonical_title,
                slug=slug,
                code=code,
                original_language=(original_language or "").strip(),
                status=WorkStatus.CREATED,
            )
        except IntegrityError as exc:
            raise DuplicateWorkError("Esta obra já está cadastrada para o autor.") from exc
    logger.info("work_created id=%s code=%s author_id=%s", work.pk, work.code, author.pk)
    return work
