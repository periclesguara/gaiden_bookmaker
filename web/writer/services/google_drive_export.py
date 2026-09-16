from __future__ import annotations

import json
import os
from pathlib import Path

from django.db import transaction

from ..models import Chapter


def _credentials():
    from .google_docs import _credentials
    return _credentials()


def _drive():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_credentials(), cache_discovery=False)


def _find_or_create_folder(drive, name: str, parent: str | None) -> dict:
    escaped = name.replace("'", "\\'")
    query = f"name = '{escaped}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent:
        query += f" and '{parent}' in parents"
    found = drive.files().list(q=query, fields="files(id,name,webViewLink)", pageSize=10).execute().get("files", [])
    if found:
        return found[0]
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent:
        body["parents"] = [parent]
    return drive.files().create(body=body, fields="id,name,webViewLink").execute()


def _find_file(drive, name: str, parent: str, mime_type: str, contract_sha: str) -> dict | None:
    escaped = name.replace("'", "\\'")
    query = (
        f"name = '{escaped}' and '{parent}' in parents and "
        f"mimeType = '{mime_type}' and trashed = false"
    )
    found = drive.files().list(
        q=query,
        fields="files(id,name,mimeType,webViewLink,appProperties)",
        pageSize=10,
    ).execute().get("files", [])
    for item in found:
        properties = item.get("appProperties") or {}
        if not properties or properties.get("contract_sha256") == contract_sha:
            return item
    return None


def export_chapter_package(chapter: Chapter) -> Chapter:
    """Transport an existing frozen package; never rebuilds its contract."""
    if chapter.status not in {Chapter.Status.PACKAGE_READY, Chapter.Status.EXPORTED_TO_DRIVE, Chapter.Status.WAITING_FOR_OUTPUT}:
        raise ValueError("chapter must be PACKAGE_READY before Drive export")
    contract = chapter.narrative_contracts.filter(version=chapter.contract_version).first()
    if not contract or not contract.frozen_at:
        raise ValueError("frozen Narrative Contract is required")
    package = Path(chapter.contract_json_path).resolve(strict=True)
    package_payload = json.loads(package.read_text(encoding="utf-8"))
    canonical_package = json.dumps(package_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    actual_sha = __import__("hashlib").sha256(canonical_package.encode()).hexdigest()
    if actual_sha != contract.payload_sha256:
        raise ValueError("frozen contract JSON SHA mismatch")
    try:
        drive = _drive()
        if chapter.drive_folder_id:
            chapter_folder = drive.files().get(
                fileId=chapter.drive_folder_id,
                fields="id,name,webViewLink,mimeType,trashed",
            ).execute()
            if chapter_folder.get("trashed") or chapter_folder.get("mimeType") != "application/vnd.google-apps.folder":
                raise ValueError("registered Drive folder is not available")
        else:
            project_folder = _find_or_create_folder(drive, f"BOOK_{chapter.project_id}", None)
            chapter_folder = _find_or_create_folder(drive, f"Chapter_{chapter.number:02d}", project_folder["id"])
        # Existing IDs are reused; creation is only attempted when absent.
        def create_file(name, mime, content=None):
            body = {"name": name, "mimeType": mime, "parents": [chapter_folder["id"]], "appProperties": {"contract_sha256": contract.payload_sha256}}
            if content is not None:
                from googleapiclient.http import MediaFileUpload
                return drive.files().create(body=body, media_body=MediaFileUpload(str(content), mimetype="application/json"), fields="id,webViewLink").execute()
            return drive.files().create(body=body, fields="id,webViewLink").execute()
        contract_name = f"Chapter_{chapter.number:02d}_CONTRACT"
        json_name = f"Chapter_{chapter.number:02d}_CONTRACT.json"
        output_name = f"Chapter_{chapter.number:02d}_OUTPUT"
        contract_doc = {"id": chapter.drive_contract_id, "webViewLink": chapter.drive_contract_url} if chapter.drive_contract_id else (_find_file(drive, contract_name, chapter_folder["id"], "application/vnd.google-apps.document", contract.payload_sha256) or create_file(contract_name, "application/vnd.google-apps.document"))
        json_file = {"id": chapter.drive_json_id, "webViewLink": ""} if chapter.drive_json_id else (_find_file(drive, json_name, chapter_folder["id"], "application/json", contract.payload_sha256) or create_file(json_name, "application/json", package))
        output_doc = {"id": chapter.drive_output_id, "webViewLink": chapter.drive_output_url} if chapter.drive_output_id else (_find_file(drive, output_name, chapter_folder["id"], "application/vnd.google-apps.document", contract.payload_sha256) or create_file(output_name, "application/vnd.google-apps.document"))
        with transaction.atomic():
            current = Chapter.objects.select_for_update().get(pk=chapter.pk)
            current.drive_folder_id = chapter_folder["id"]
            current.drive_folder_url = chapter_folder.get("webViewLink", "")
            current.drive_contract_id = contract_doc["id"]
            current.drive_contract_url = contract_doc.get("webViewLink", "")
            current.drive_json_id = json_file["id"]
            current.drive_output_id = output_doc["id"]
            current.drive_output_url = output_doc.get("webViewLink", "")
            current.drive_export_error = ""
            current.status = current.Status.WAITING_FOR_OUTPUT
            current.save(update_fields=("drive_folder_id", "drive_folder_url", "drive_contract_id", "drive_contract_url", "drive_json_id", "drive_output_id", "drive_output_url", "drive_export_error", "status", "updated_at"))
            return current
    except Exception as exc:
        chapter.drive_export_error = str(exc)[:2000]
        chapter.save(update_fields=("drive_export_error", "updated_at"))
        raise
