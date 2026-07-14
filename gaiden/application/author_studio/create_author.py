from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.utils.text import slugify

from gaiden.domain.author_studio.codes import canonicalize, generate_author_code
from gaiden.domain.author_studio.exceptions import DuplicateAuthorError

logger = logging.getLogger(__name__)


def create_author(name: str):
    from author_studio.models import Author

    display_name = " ".join((name or "").split())
    canonical_name = canonicalize(display_name)
    if not canonical_name:
        raise ValueError("Informe um nome de autor válido.")
    with transaction.atomic():
        if Author.objects.filter(canonical_name=canonical_name).exists():
            raise DuplicateAuthorError("Este autor já está cadastrado.")
        code = generate_author_code(display_name, lambda candidate: Author.objects.filter(code=candidate).exists())
        slug_base = slugify(display_name) or code.lower()
        slug = slug_base
        number = 2
        while Author.objects.filter(slug=slug).exists():
            slug = f"{slug_base}-{number}"
            number += 1
        try:
            author = Author.objects.create(
                name=display_name,
                canonical_name=canonical_name,
                slug=slug,
                code=code,
            )
        except IntegrityError as exc:
            raise DuplicateAuthorError("Este autor já está cadastrado.") from exc
    logger.info("author_created id=%s code=%s", author.pk, author.code)
    return author
