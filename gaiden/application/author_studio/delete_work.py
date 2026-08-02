from __future__ import annotations

import logging

from django.db import transaction

logger = logging.getLogger(__name__)


def delete_work(*, work) -> tuple[str, str, str]:
    """Delete one work and its DB dependents; stored artifacts remain traceable on disk."""
    from author_studio.models import CanonicalText, Work, WorkSource

    with transaction.atomic():
        locked = Work.objects.select_for_update().select_related("author").get(pk=work.pk)
        author_slug = locked.author.slug
        code = locked.code
        title = locked.title
        CanonicalText.objects.filter(work=locked).delete()
        WorkSource.objects.filter(work=locked).delete()
        locked.delete()
    logger.info("work_deleted code=%s title=%s", code, title)
    return author_slug, code, title
