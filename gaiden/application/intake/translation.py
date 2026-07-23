from __future__ import annotations

import hashlib
import json

from gaiden.domain.intake import IntakeState
from gaiden.infrastructure import intake_storage

from .workflow import transition_item


def _manifest_for_item(item) -> tuple[dict, object]:
    if not item.target_language:
        raise ValueError("Target language is required")
    expected = intake_storage.translation_return_path(
        item.batch.code, item.batch.source_language, item.order_index, item.target_language
    )
    manifest = {
        "batch_code": item.batch.code,
        "item_id": item.id,
        "source_filename": item.source_filename,
        "source_sha256": item.source_sha256,
        "source_language": item.batch.source_language,
        "target_language": item.target_language,
        "clean_path": item.clean_path,
        "expected_return_path": intake_storage.relative_storage_path(expected),
    }
    return manifest, expected


def prepare_for_codex(item, *, target_language: str | None = None) -> dict:
    if item.duplicate_of_id:
        raise ValueError(f"Duplicate item must use canonical item {item.duplicate_of_id}")
    if item.status != IntakeState.CLEAN_READY.value:
        raise ValueError("Item must be CLEAN_READY before preparing for Codex")
    if target_language is not None:
        item.target_language = intake_storage.safe_segment(target_language, label="target language")
    if not item.target_language:
        raise ValueError("Target language is required")
    clean_source = intake_storage.resolve_stored_path(item.clean_path)
    clean_payload = clean_source.read_bytes()
    input_path = intake_storage.translation_input_path(
        item.batch.code, item.batch.source_language, item.order_index, item.target_language
    )
    intake_storage.atomic_write_bytes(input_path, clean_payload)
    manifest, expected_return = _manifest_for_item(item)
    intake_storage.atomic_write_json(
        intake_storage.translation_manifest_path(
            item.batch.code, item.batch.source_language, item.order_index, item.target_language
        ),
        manifest,
    )
    root_manifest_path = intake_storage.manifest_path(item.batch.code, item.batch.source_language)
    root_manifest = {"batch_code": item.batch.code, "items": []}
    if root_manifest_path.exists():
        root_manifest = json.loads(root_manifest_path.read_text(encoding="utf-8"))
    root_manifest["items"] = [row for row in root_manifest.get("items", []) if row.get("item_id") != item.id]
    root_manifest["items"].append(manifest)
    root_manifest["items"].sort(key=lambda row: row["item_id"])
    intake_storage.atomic_write_json(root_manifest_path, root_manifest, overwrite=True)
    item.translation_input_path = intake_storage.relative_storage_path(input_path)
    item.translation_return_path = intake_storage.relative_storage_path(expected_return)
    item.save(
        update_fields=["target_language", "translation_input_path", "translation_return_path", "updated_at"]
    )
    transition_item(item, IntakeState.READY_FOR_CODEX)
    return manifest


def register_translation_return(item, filename: str, payload: bytes) -> str:
    if item.status not in {IntakeState.READY_FOR_CODEX.value, IntakeState.TRANSLATING.value}:
        raise ValueError("Item is not waiting for a translation return")
    manifest_path = intake_storage.translation_manifest_path(
        item.batch.code, item.batch.source_language, item.order_index, item.target_language
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = intake_storage.resolve_stored_path(manifest["expected_return_path"])
    if filename != expected.name:
        raise ValueError(f"Unexpected return filename; expected {expected.name}")
    if manifest.get("item_id") != item.id or manifest.get("target_language") != item.target_language:
        raise ValueError("Translation return does not match item manifest")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Translation return must be valid UTF-8") from exc
    if not text.strip():
        raise ValueError("Translation return cannot be empty")
    intake_storage.atomic_write_bytes(expected, payload)
    digest = hashlib.sha256(payload).hexdigest()
    manifest["return_sha256"] = digest
    intake_storage.atomic_write_json(manifest_path, manifest, overwrite=True)
    item.translation_return_path = intake_storage.relative_storage_path(expected)
    item.save(update_fields=["translation_return_path", "updated_at"])
    transition_item(item, IntakeState.TRANSLATION_RETURNED)
    return digest


def confirm_ready_for_editing(item) -> None:
    transition_item(item, IntakeState.READY_FOR_EDITING)
