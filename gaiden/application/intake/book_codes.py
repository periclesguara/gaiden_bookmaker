from __future__ import annotations

import re

from django.db import connection, transaction


BOOK_CODE_RE = re.compile(r"^book_(\d+)$")
LOCK_KEY = "gaiden:intake:book-code:v1"


def _number(value: str) -> int:
    match = BOOK_CODE_RE.fullmatch(value or "")
    return int(match.group(1)) if match else 0


def assign_book_code(item) -> str:
    if item.book_code:
        return item.book_code
    Work = item._meta.apps.get_model("editorial", "Work")
    IntakeItem = type(item)
    with transaction.atomic():
        locked = IntakeItem.objects.select_for_update().get(pk=item.pk)
        if locked.book_code:
            return locked.book_code
        if connection.vendor != "postgresql":
            raise RuntimeError("Atomic book-code allocation requires PostgreSQL")
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [LOCK_KEY])
        values = list(Work.objects.values_list("code", flat=True))
        values.extend(IntakeItem.objects.exclude(book_code="").values_list("book_code", flat=True))
        next_number = max((_number(value) for value in values), default=0) + 1
        locked.book_code = f"book_{next_number:04d}"
        locked.save(update_fields=["book_code", "updated_at"])
        item.book_code = locked.book_code
        return locked.book_code
