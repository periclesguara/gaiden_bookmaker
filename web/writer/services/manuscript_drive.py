"""Read-only final-file selection through the Writer's existing Drive client."""
from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone

from ..models import StoryProject
from .google_drive_export import _drive
from .manuscripts import (MAX_MANUSCRIPT_BYTES, _immutable_write, ensure_project_writable,
                         import_manuscript_revision)

_ID = re.compile(r"^[A-Za-z0-9_-]{10,255}$")
_TEXT_MIMES = {"text/plain", "text/markdown", "text/x-markdown"}
_GOOGLE_DOC = "application/vnd.google-apps.document"


def drive_id(value):
    value = (value or "").strip()
    for pattern in (r"https://drive\.google\.com/file/d/([A-Za-z0-9_-]+)(?:/.*)?$",
                    r"https://docs\.google\.com/document/d/([A-Za-z0-9_-]+)(?:/.*)?$"):
        match = re.fullmatch(pattern, value)
        if match:
            value = match.group(1)
            break
    if not _ID.fullmatch(value):
        raise ValueError("informe um ID ou link válido de arquivo do Google Drive")
    return value


def _supported(metadata):
    name = str(metadata.get("name", ""))
    mime = metadata.get("mimeType")
    return mime == _GOOGLE_DOC or (
        Path(name).suffix.lower() in {".txt", ".md"}
        and mime in _TEXT_MIMES | {"application/octet-stream"}
    )


def list_final_files(project, page_token=None):
    """List one bounded page in the configured project folder; do not download."""
    folder = drive_id(project.drive_project_folder_id)
    result = _drive().files().list(
        q=f"'{folder}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'",
        fields="nextPageToken,files(id,name,mimeType,modifiedTime,size)",
        pageSize=100, pageToken=page_token or None, orderBy="modifiedTime desc",
    ).execute()
    return [item for item in result.get("files", []) if _supported(item)], result.get("nextPageToken", "")


class _BoundedBuffer(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_MANUSCRIPT_BYTES:
            raise ValueError("manuscrito do Drive excede o limite de 50 MiB")
        return super().write(data)


def _download(drive, metadata):
    from googleapiclient.http import MediaIoBaseDownload

    if int(metadata.get("size") or 0) > MAX_MANUSCRIPT_BYTES:
        raise ValueError("manuscrito do Drive excede o limite de 50 MiB")
    if metadata["mimeType"] == _GOOGLE_DOC:
        request = drive.files().export_media(fileId=metadata["id"], mimeType="text/plain")
    else:
        request = drive.files().get_media(fileId=metadata["id"])
    buffer = _BoundedBuffer()
    downloader = MediaIoBaseDownload(buffer, request, chunksize=1024 * 1024)
    for _ in range(MAX_MANUSCRIPT_BYTES // (1024 * 1024) + 2):
        _, done = downloader.next_chunk(num_retries=0)
        if done:
            return buffer.getvalue()
    raise ValueError("o download do Drive não foi concluído dentro do limite de chunks")


def import_drive_final(project, selected_file, created_by=None):
    """Download exactly the selected file, then reuse the local revision importer."""
    ensure_project_writable(project)
    file_id = drive_id(selected_file)
    drive = _drive()
    fields = "id,name,mimeType,size,modifiedTime,version,md5Checksum,trashed"
    metadata = drive.files().get(fileId=file_id, fields=fields).execute()
    if metadata.get("trashed") or not _supported(metadata):
        raise ValueError("selecione um arquivo .txt, .md ou Google Docs disponível")
    data = _download(drive, metadata)
    after = drive.files().get(fileId=file_id, fields=fields).execute()
    if any(after.get(k) != metadata.get(k) for k in ("version", "modifiedTime", "md5Checksum", "trashed")):
        raise ValueError("o arquivo do Drive mudou durante a importação; selecione novamente")
    if metadata.get("md5Checksum") and hashlib.md5(data).hexdigest() != metadata["md5Checksum"]:
        raise ValueError("a integridade do arquivo do Drive não confere")
    name = Path(metadata["name"]).name
    if metadata["mimeType"] == _GOOGLE_DOC:
        name = name[:240] + ".txt"
    path = None
    sidecar = None
    sidecar_created = False
    try:
        with transaction.atomic():
            project = StoryProject.objects.select_for_update().get(pk=project.pk)
            ensure_project_writable(project)
            # A retry of the same file/version reuses its existing immutable revision.
            previous = project.manuscript_revisions.filter(source_kind="IMPORTED_FINAL").order_by("-version")
            download_sha = hashlib.sha256(data).hexdigest()
            for revision in previous:
                provenance = Path(revision.storage_path).with_suffix(".source.json")
                if provenance.is_file():
                    recorded = json.loads(provenance.read_text(encoding="utf-8"))
                    if (recorded.get("drive_file_id") == file_id
                            and recorded.get("download_sha256") == download_sha
                            and recorded.get("drive_version") == metadata.get("version")):
                        from .manuscripts import revision_bytes
                        revision_bytes(project, revision)
                        return revision
            revision = import_manuscript_revision(project, SimpleUploadedFile(name, data), created_by)
            path = Path(revision.storage_path)
            sidecar = path.with_suffix(".source.json")
            _immutable_write(sidecar, (json.dumps({
                "schema": "writer_final_import_v1", "source": "google_drive",
                "project_id": project.pk, "revision_id": revision.pk,
                "drive_file_id": file_id, "name": metadata["name"],
                "drive_version": metadata.get("version"), "modified_at": metadata.get("modifiedTime"),
                "imported_at": timezone.now().isoformat(),
                "download_sha256": download_sha, "sha256": revision.sha256,
            }, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            sidecar_created = True
            project.manuscript_drive_id = file_id
            project.manuscript_drive_url = f"https://drive.google.com/file/d/{file_id}/view"
            project.manuscript_drive_sha256 = revision.sha256
            project.manuscript_drive_imported_at = timezone.now()
            project.save(update_fields=("manuscript_drive_id", "manuscript_drive_url",
                                       "manuscript_drive_sha256", "manuscript_drive_imported_at", "updated_at"))
        return revision
    except BaseException:
        if path is not None:
            path.unlink(missing_ok=True)
        # Do not remove a preexisting operator sidecar if publication failed.
        if sidecar_created:
            sidecar.unlink(missing_ok=True)
        raise
