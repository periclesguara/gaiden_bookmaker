from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from django.db import IntegrityError, transaction
from django.utils import timezone

from collections_module.models import Collection
from editorial.models import Work
from gaiden.domain.intake import IntakeState
from gaiden.infrastructure import intake_storage
from pipeline.models import BookEditionTemplate, PipelineJob
from web.intake_module.models import BookCodeSequence, IntakeBatch, IntakeItem

from .ingestion import ACCEPTED_SUFFIXES
from .reconciliation import inspect_original_artifact


BOOK_CODE_PATTERN = re.compile(r"^book_[0-9]{4,}$")
BOOK_CODE_SEQUENCE_NAME = "book"
BOOK_CODE_INITIAL_NUMBER = 33
ELIGIBLE_STATES = {
    IntakeState.DISCOVERED.value,
    IntakeState.DOWNLOADED.value,
    IntakeState.CLEAN_READY.value,
}


class BookCodeAllocationError(ValueError):
    pass


class BookCodeAllocationConflict(BookCodeAllocationError):
    pass


class StaleBookCodePlan(BookCodeAllocationError):
    pass


class BookCodeManifestConflict(BookCodeAllocationError):
    pass


def format_book_code(number: int) -> str:
    if number < 1:
        raise BookCodeAllocationError("Book code number must be positive")
    return f"book_{number:04d}"


def allocation_manifest_path(batch: IntakeBatch) -> Path:
    return intake_storage.book_code_allocation_manifest_path(
        batch.code,
        batch.source_language,
    )


def _all_canonical_codes() -> set[str]:
    codes = set(
        IntakeItem.objects.exclude(book_code="").values_list("book_code", flat=True)
    )
    codes.update(Work.objects.values_list("code", flat=True))
    codes.update(
        BookEditionTemplate.objects.exclude(book_code="").values_list(
            "book_code", flat=True
        )
    )
    codes.update(
        PipelineJob.objects.exclude(book_code="").values_list("book_code", flat=True)
    )
    codes.update(
        Collection.objects.exclude(pipeline_book_code="").values_list(
            "pipeline_book_code", flat=True
        )
    )
    return {str(code).strip() for code in codes if str(code).strip()}


def _registered_codes_for_identity(title: str, author: str) -> list[str]:
    if not title:
        return []
    works = Work.objects.filter(title__iexact=title).select_related("author")
    templates = BookEditionTemplate.objects.filter(title__iexact=title)
    if author:
        works = works.filter(author__name__iexact=author)
        templates = templates.filter(author_name__iexact=author)
    codes = set(works.values_list("code", flat=True))
    codes.update(templates.values_list("book_code", flat=True))
    return sorted(code for code in codes if code)


def _existing_identity_conflict(item: IntakeItem, title: str) -> str:
    if not item.book_code:
        return ""
    work = Work.objects.filter(code=item.book_code).select_related("author").first()
    if work is not None:
        if item.confirmed_title and work.title.strip().casefold() != title.casefold():
            return "book_code belongs to a Work with a different title"
        author = (item.batch.author_default or "").strip()
        if author and work.author.name.strip().casefold() != author.casefold():
            return "book_code belongs to a Work with a different author"
    templates = list(BookEditionTemplate.objects.filter(book_code=item.book_code))
    if item.confirmed_title and templates and not any(
        template.title.strip().casefold() == title.casefold() for template in templates
    ):
        return "book_code belongs to a BookEditionTemplate with a different title"
    if item.handoff_edition_id:
        edition = item.handoff_edition_id
        linked = Work.objects.filter(editions__id=edition, code=item.book_code).exists()
        if not linked:
            return "handoff edition diverges from Intake book_code"
    return ""


def _compatible_artifact(item: IntakeItem) -> tuple[bool, str]:
    suffix = Path(item.source_filename).suffix.lower()
    if (
        suffix not in ACCEPTED_SUFFIXES
        or item.source_format.lower() != suffix.lstrip(".")
    ):
        return False, "arquivo incompatível"
    if item.source_size < 1:
        return False, "arquivo vazio ou tamanho desconhecido"
    if item.status == IntakeState.DISCOVERED.value:
        return True, ""
    inspection = inspect_original_artifact(item)
    if not inspection.valid:
        return False, inspection.reason or "arquivo inválido"
    return True, ""


def _find_contiguous_codes(start_number: int, count: int, used_codes: set[str]) -> list[str]:
    if count < 1:
        return []
    candidate = max(BOOK_CODE_INITIAL_NUMBER, start_number)
    while True:
        proposed = [
            format_book_code(number)
            for number in range(candidate, candidate + count)
        ]
        conflicts = [index for index, code in enumerate(proposed) if code in used_codes]
        if not conflicts:
            return proposed
        candidate += conflicts[0] + 1


def _plan_token(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def preview_book_code_allocation(
    batch: IntakeBatch,
    *,
    items: list[IntakeItem] | None = None,
    sequence: BookCodeSequence | None = None,
) -> dict:
    if items is None:
        batch = IntakeBatch.objects.get(pk=batch.pk)
    ordered_items = (
        items
        if items is not None
        else list(
            batch.items.select_related("batch", "duplicate_of").order_by(
                "order_index", "id"
            )
        )
    )
    sequence = sequence or BookCodeSequence.objects.filter(
        name=BOOK_CODE_SEQUENCE_NAME
    ).first()
    next_number = sequence.next_number if sequence else BOOK_CODE_INITIAL_NUMBER
    used_codes = _all_canonical_codes()
    code_counts = Counter(item.book_code for item in ordered_items if item.book_code)
    hash_groups: dict[str, list[IntakeItem]] = defaultdict(list)
    for item in ordered_items:
        if item.source_sha256:
            hash_groups[item.source_sha256].append(item)

    rows: list[dict] = []
    eligible_rows: list[dict] = []
    conflicts: list[dict] = []
    for item in ordered_items:
        title = (item.confirmed_title or item.suggested_title or "").strip()
        row = {
            "item_id": item.id,
            "order_index": item.order_index,
            "source_filename": item.source_filename,
            "source_sha256": item.source_sha256,
            "title": title,
            "status": "",
            "status_label": "",
            "current_code": item.book_code,
            "proposed_code": "",
            "reason": "",
        }
        identity_conflict = _existing_identity_conflict(item, title)
        duplicate_group = hash_groups.get(item.source_sha256, [])
        unresolved_duplicate = bool(
            item.source_sha256
            and len(duplicate_group) > 1
            and not any(candidate.duplicate_of_id for candidate in duplicate_group)
        )
        if item.book_code:
            if code_counts[item.book_code] > 1:
                identity_conflict = "book_code is duplicated inside this batch"
            if identity_conflict:
                row.update(
                    status="conflict",
                    status_label="Conflito",
                    reason=identity_conflict,
                )
                conflicts.append(row)
            else:
                row.update(
                    status="numbered",
                    status_label="Já numerado",
                    proposed_code="Preservar",
                )
        elif item.handoff_edition_id or item.handed_off_at:
            row.update(
                status="conflict",
                status_label="Conflito",
                reason="item enviado ao Bookmaker/handoff sem book_code",
            )
            conflicts.append(row)
        elif item.duplicate_of_id:
            row.update(
                status="duplicate",
                status_label="Duplicata",
                reason="item duplicado",
            )
        elif unresolved_duplicate:
            row.update(
                status="conflict",
                status_label="Conflito",
                reason="hash duplicado sem vínculo duplicate_of",
            )
            conflicts.append(row)
        elif item.status not in ELIGIBLE_STATES:
            row.update(
                status="ineligible",
                status_label="Estado não elegível",
                reason=item.status,
            )
        elif not title:
            row.update(
                status="missing_title",
                status_label="Título pendente",
                reason="título sugerido ou confirmado obrigatório",
            )
        else:
            valid, reason = _compatible_artifact(item)
            if not valid:
                row.update(
                    status="invalid_file",
                    status_label="Arquivo inválido",
                    reason=reason,
                )
            else:
                registered_codes = _registered_codes_for_identity(
                    title,
                    (batch.author_default or "").strip(),
                )
                if len(registered_codes) > 1:
                    row.update(
                        status="conflict",
                        status_label="Conflito",
                        reason="obra corresponde a múltiplos códigos cadastrados",
                    )
                    conflicts.append(row)
                elif registered_codes:
                    row.update(
                        status="registered",
                        status_label="Já cadastrado",
                        current_code=registered_codes[0],
                        proposed_code="Preservar",
                    )
                else:
                    row.update(status="eligible", status_label="Elegível")
                    eligible_rows.append(row)
        rows.append(row)

    proposals = _find_contiguous_codes(next_number, len(eligible_rows), used_codes)
    for row, code in zip(eligible_rows, proposals):
        row["proposed_code"] = code

    token_payload = {
        "batch": {
            "id": batch.id,
            "code": batch.code,
            "source_language": batch.source_language,
            "updated_at": batch.updated_at.isoformat() if batch.updated_at else "",
        },
        "sequence_next_number": next_number,
        "used_codes": sorted(used_codes),
        "rows": rows,
    }
    plan_sha256 = _plan_token(token_payload)
    numbered_count = sum(row["status"] == "numbered" for row in rows)
    duplicate_count = sum(row["status"] == "duplicate" for row in rows)
    handed_off_count = sum(
        bool(item.handoff_edition_id or item.handed_off_at) for item in ordered_items
    )
    return {
        "batch_id": batch.id,
        "batch_code": batch.code,
        "rows": rows,
        "plan_sha256": plan_sha256,
        "eligible_count": len(eligible_rows),
        "numbered_count": numbered_count,
        "duplicate_count": duplicate_count,
        "handed_off_count": handed_off_count,
        "total_count": len(rows),
        "start_code": proposals[0] if proposals else "",
        "end_code": proposals[-1] if proposals else "",
        "conflicts": conflicts,
        "no_op": not proposals and not conflicts,
        "manifest_path": allocation_manifest_path(batch),
        "manifest_exists": allocation_manifest_path(batch).is_file(),
    }


def _manifest_payload(batch: IntakeBatch, plan: dict, allocated_at, actor: str) -> dict:
    allocated_rows = [
        row for row in plan["rows"] if row["proposed_code"].startswith("book_")
    ]
    return {
        "batch_code": batch.code,
        "start_code": plan["start_code"],
        "end_code": plan["end_code"],
        "allocated_count": len(allocated_rows),
        "plan_sha256": plan["plan_sha256"],
        "created_at": allocated_at.isoformat(),
        "created_by": actor,
        "items": [
            {
                "item_id": row["item_id"],
                "order_index": row["order_index"],
                "source_filename": row["source_filename"],
                "source_sha256": row["source_sha256"],
                "book_code": row["proposed_code"],
            }
            for row in allocated_rows
        ],
    }


def _persist_manifest(path: Path, payload: dict) -> None:
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BookCodeManifestConflict("Existing allocation manifest is invalid") from exc
        comparable_keys = {
            "batch_code",
            "start_code",
            "end_code",
            "allocated_count",
            "plan_sha256",
            "items",
        }
        if {key: existing.get(key) for key in comparable_keys} == {
            key: payload.get(key) for key in comparable_keys
        }:
            return
        raise BookCodeManifestConflict(
            "Existing book_code_allocation.json diverges from the confirmed plan"
        )
    intake_storage.atomic_write_json(path, payload)


def reserve_book_codes(batch: IntakeBatch, *, plan_sha256: str, actor: str = "") -> dict:
    try:
        with transaction.atomic():
            sequence, _ = BookCodeSequence.objects.get_or_create(
                name=BOOK_CODE_SEQUENCE_NAME,
                defaults={"next_number": BOOK_CODE_INITIAL_NUMBER},
            )
            sequence = BookCodeSequence.objects.select_for_update().get(pk=sequence.pk)
            locked_batch = IntakeBatch.objects.select_for_update().get(pk=batch.pk)
            locked_items = list(
                locked_batch.items.select_for_update()
                .select_related("batch")
                .order_by("order_index", "id")
            )
            plan = preview_book_code_allocation(
                locked_batch,
                items=locked_items,
                sequence=sequence,
            )
            if plan["plan_sha256"] != plan_sha256:
                raise StaleBookCodePlan(
                    "O lote mudou desde a pré-visualização; gere uma nova sequência."
                )
            if plan["conflicts"]:
                raise BookCodeAllocationConflict(
                    "A reserva foi bloqueada por conflitos de identidade editorial."
                )
            proposed_rows = [
                row for row in plan["rows"] if row["proposed_code"].startswith("book_")
            ]
            if not proposed_rows:
                return {"before": plan, "after": plan, "no_op": True, "allocated": []}

            allocated_at = timezone.now()
            items_by_id = {item.id: item for item in locked_items}
            for row in proposed_rows:
                item = items_by_id[row["item_id"]]
                if item.book_code:
                    raise BookCodeAllocationConflict(
                        f"Item {item.id} received a book_code after preview"
                    )
                if not BOOK_CODE_PATTERN.fullmatch(row["proposed_code"]):
                    raise BookCodeAllocationConflict(
                        "Proposed book_code has invalid format"
                    )
                item.book_code = row["proposed_code"]
                item.book_code_reserved_at = allocated_at
                item.book_code_reserved_by = actor
                item.save(
                    update_fields=[
                        "book_code",
                        "book_code_reserved_at",
                        "book_code_reserved_by",
                        "updated_at",
                    ]
                )

            last_number = int(proposed_rows[-1]["proposed_code"].split("_", 1)[1])
            sequence.next_number = last_number + 1
            sequence.save(update_fields=["next_number", "updated_at"])
            locked_batch.book_codes_reserved_at = allocated_at
            locked_batch.book_codes_reserved_by = actor
            locked_batch.book_codes_start = plan["start_code"]
            locked_batch.book_codes_end = plan["end_code"]
            locked_batch.book_codes_allocated_count = len(proposed_rows)
            locked_batch.book_code_plan_sha256 = plan["plan_sha256"]
            locked_batch.save(
                update_fields=[
                    "book_codes_reserved_at",
                    "book_codes_reserved_by",
                    "book_codes_start",
                    "book_codes_end",
                    "book_codes_allocated_count",
                    "book_code_plan_sha256",
                    "updated_at",
                ]
            )
            manifest = _manifest_payload(locked_batch, plan, allocated_at, actor)
            _persist_manifest(allocation_manifest_path(locked_batch), manifest)
            after = {
                **plan,
                "numbered_count": plan["numbered_count"] + len(proposed_rows),
                "eligible_count": 0,
                "no_op": False,
                "manifest_exists": True,
            }
            return {
                "before": plan,
                "after": after,
                "no_op": False,
                "allocated": [row["proposed_code"] for row in proposed_rows],
                "manifest": manifest,
            }
    except IntegrityError as exc:
        raise BookCodeAllocationConflict(
            "A book_code collision prevented the reservation; no item was changed."
        ) from exc
