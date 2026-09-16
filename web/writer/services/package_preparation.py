from __future__ import annotations

import json
import os
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from .execution_order import chapter_is_sequentially_eligible, freeze_contract
from .normalization import writer_storage_root


def prepare_chapter_package(chapter):
    """Freeze and persist the package; deliberately performs no model/API call."""
    eligible, reason = chapter_is_sequentially_eligible(chapter)
    if not eligible:
        raise ValueError(reason)
    contract = freeze_contract(chapter)
    root = writer_storage_root() / "packages" / f"project-{chapter.project_id}" / f"Chapter_{chapter.number:02d}"
    root.mkdir(parents=True, exist_ok=True)
    contract_path = root / f"Chapter_{chapter.number:02d}_CONTRACT.json"
    contract_path.write_text(json.dumps(contract.payload_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with transaction.atomic():
        current = type(chapter).objects.select_for_update().get(pk=chapter.pk)
        current.contract_version = contract.version
        current.contract_json_path = str(contract_path)
        current.status = current.Status.PACKAGE_READY
        current.error_message = ""
        current.save(update_fields=("contract_version", "contract_json_path", "status", "error_message", "updated_at"))
    return current, contract, root
