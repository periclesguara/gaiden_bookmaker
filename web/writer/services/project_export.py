from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import os

from django.db import transaction
from django.utils import timezone as django_timezone

from ..models import Chapter, StoryProject, WriterExport
from ..reference_bible import effective_reference_bible
from .book_kit import build_book_kit
from .google_drive_export import _drive, _find_or_create_folder
from .manuscripts import ensure_project_writable

GOOGLE_FOLDER = "application/vnd.google-apps.folder"
TEXT_MIME = "text/markdown"
EXPECTED_EXTENSION = ".md"
CONTRACT_VERSION = "2.0"


@dataclass(frozen=True)
class ReturnFile:
    chapter: Chapter
    expected: dict
    drive_file: dict
    text: str
    sha256: str
    word_count: int
    duplicate: bool
    metadata: dict


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return cleaned or "Project"


def _drive_url(file_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{file_id}"


def _file_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def _expected_filename(chapter: Chapter) -> str:
    return f"chapter_{chapter.number:02d}_response{EXPECTED_EXTENSION}"


def _next_export_version(project: StoryProject, chapter: Chapter) -> int:
    versions = []
    for prior in project.writer_exports.only("expected_files"):
        for row in prior.expected_files:
            if str(row.get("chapter_id", "")) == str(chapter.pk):
                versions.append(int(row.get("export_version", 1)))
    return max(versions, default=0) + 1


def _expected_row(chapter: Chapter, *, export_version: int) -> dict:
    return {
        "project_id": str(chapter.project_id),
        "chapter_id": str(chapter.pk),
        "chapter_number": chapter.number,
        "chapter_title": chapter.title,
        "filename": _expected_filename(chapter),
        "metadata_filename": f"chapter_{chapter.number:02d}_response.json",
        "export_version": export_version,
        "contract_version": CONTRACT_VERSION,
        "state": "NOT_EXPORTED",
        "target_words": chapter.target_words,
        "format": EXPECTED_EXTENSION.lstrip("."),
    }


def _create_text_file(drive, parent_id: str, name: str, text: str, *, app_properties: dict | None = None, mime_type: str = TEXT_MIME) -> dict:
    from googleapiclient.http import MediaInMemoryUpload

    media = MediaInMemoryUpload(text.encode("utf-8"), mimetype=mime_type, resumable=False)
    body = {"name": name, "parents": [parent_id], "mimeType": mime_type}
    if app_properties:
        body["appProperties"] = app_properties
    return drive.files().create(
        body=body,
        media_body=media,
        fields="id,name,webViewLink,appProperties",
    ).execute()


def _global_contract(project: StoryProject) -> str:
    kit = build_book_kit(project)
    return "\n\n".join([
        "# GLOBAL PRODUCTION CONTRACT",
        f"PROJECT TITLE: {project.title}",
        f"LANGUAGE: {project.language}",
        "## REFERENCE BIBLE",
        effective_reference_bible(project),
        "## BOOK KIT",
        json.dumps(kit, ensure_ascii=False, indent=2),
        "## STORY DIRECTION",
        project.story_direction or "",
        "## STORY OUTLINE",
        project.story_outline or "",
        "## QUALITY RULES",
        "- Produce only finished chapter prose in the expected return files.",
        "- Do not mention prompts, Gaiden, ChatGPT, Gemini or the generation process.",
        "- Preserve canon, chronology, voice, point of view and chapter order.",
        "- Do not approve or finalize chapters; Writer handles import, review and finalization.",
    ]).strip() + "\n"


def _chapter_contract(project: StoryProject, chapter: Chapter, export: WriterExport) -> str:
    expected = export.expected_files[0]
    response_metadata = {
        "project_id": str(project.pk),
        "chapter_id": str(chapter.pk),
        "export_id": export.export_id,
        "export_version": expected["export_version"],
        "contract_version": CONTRACT_VERSION,
        "status": "COMPLETED",
        "content_file": _expected_filename(chapter),
        "word_count": "REPLACE_WITH_WORD_COUNT",
        "sha256": "REPLACE_WITH_SHA256_OF_EXACT_UTF8_MD_BYTES",
    }
    return "\n\n".join([
        "# CHAPTER PRODUCTION CONTRACT",
        f"PROJECT TITLE: {project.title}",
        f"EXPORT ID: {export.export_id}",
        f"EXPORT VERSION: {expected['export_version']}",
        f"PROJECT ID: {project.pk}",
        f"CHAPTER ID: {chapter.pk}",
        f"CHAPTER NUMBER: {chapter.number:02d}",
        f"CHAPTER TITLE: {chapter.title}",
        "## NARRATIVE DIRECTION",
        chapter.direction or "",
        "## COMPLETE CHAPTER OUTLINE",
        chapter.script or "",
        f"TARGET WORD COUNT: {chapter.target_words}",
        f"EXPECTED OUTPUT FILENAME: {_expected_filename(chapter)}",
        f"EXPECTED METADATA FILENAME: {expected['metadata_filename']}",
        f"RETURN FOLDER URL: {export.return_folder_url}",
        "## REFERENCE BIBLE",
        effective_reference_bible(project),
        "## CONTINUITY REQUIREMENTS",
        "Respect previous approved Writer continuity. If no prior chapter text is available, follow the project contract and chapter outline.",
        "## RETURN INSTRUCTION",
        "Save the completed Markdown file in the exact return folder above. Do not save it in the project root, chapters folder, or another export.",
        f"1. {_expected_filename(chapter)}: complete UTF-8 chapter prose.",
        f"2. {expected['metadata_filename']} (optional): JSON metadata using the exact identity below. If provided, replace the word count and SHA-256 placeholders with the values for the final Markdown file. Compute SHA-256 from the exact UTF-8 bytes, including its final newline if present.",
        "```json\n" + json.dumps(response_metadata, ensure_ascii=False, indent=2) + "\n```",
        "Do not rename files or reuse metadata from another export. Without JSON, Writer identifies the response from this export's linked return folder and exact filename, then computes the checksum itself. If JSON exists, Writer validates its identity and checksum. Import does not approve or finalize the chapter.",
    ]).strip() + "\n"


def _readme(project: StoryProject, export: WriterExport, expected: list[dict]) -> str:
    files = "\n".join(f"- {row['filename']} — {row['chapter_title']} ({row['target_words']} words)" for row in expected)
    return "\n\n".join([
        "# READ ME FIRST — GAIDEN WRITER EXPORT",
        f"Project: {project.title}",
        f"Export ID: {export.export_id}",
        f"Export folder: {export.export_folder_url}",
        f"Return folder: {export.return_folder_url}",
        f"Language: {project.language}",
        "## Expected Return Files",
        files,
        "## External Engine Instruction",
        "**After generating each chapter, save the completed manuscript in the Google Drive return folder identified below. Do not save it in the project root, chapters folder, or another export.**",
        "## Validation",
        "Writer will validate filenames, project/export association, content, word count and duplicate imports. Import is not editorial approval.",
        "## Return Folder",
        export.return_folder_url,
    ]).strip() + "\n"


def _manifest(project: StoryProject, export: WriterExport, expected: list[dict]) -> dict:
    return {
        "schema_version": CONTRACT_VERSION,
        "contract_version": CONTRACT_VERSION,
        "project_id": str(project.pk),
        "project_title": project.title,
        "export_id": export.export_id,
        "created_at": export.created_at.isoformat(),
        "export_timestamp": export.created_at.isoformat(),
        "language": project.language,
        "chapter_count": len(expected),
        "export_folder_id": export.export_folder_id,
        "export_folder_url": export.export_folder_url,
        "return_folder_id": export.return_folder_id,
        "return_folder_url": export.return_folder_url,
        "contract_file_id": export.contract_file_id,
        "contract_file_url": export.contract_file_url,
        "expected_format": EXPECTED_EXTENSION,
        "expected_files": expected,
    }


def _set_export_state(export: WriterExport, state: str) -> None:
    export.expected_files = [{**row, "state": state} for row in export.expected_files]
    manifest = dict(export.manifest_json)
    if manifest:
        manifest["expected_files"] = export.expected_files
    export.manifest_json = manifest
    export.save(update_fields=("expected_files", "manifest_json", "updated_at"))


def _drive_error(exc: Exception, *, fallback: str, missing: str = "FILE_NOT_FOUND") -> ValueError:
    try:
        from googleapiclient.errors import HttpError
    except ImportError:  # pragma: no cover - dependency is present in production
        HttpError = ()
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", None)
        if status == 401:
            return ValueError("TOKEN_EXPIRED: Google Drive authorization must be renewed.")
        if status == 403:
            return ValueError("PERMISSION_DENIED: the connected Google account cannot access this Drive resource.")
        if status == 404:
            return ValueError(f"{missing}: the linked Google Drive resource was not found.")
    if isinstance(exc, ValueError):
        return exc
    message = str(exc)
    known = (
        "AUTH_REQUIRED", "TOKEN_EXPIRED", "PERMISSION_DENIED", "FOLDER_NOT_FOUND",
        "FILE_NOT_FOUND", "CHAPTER_MISMATCH", "VERSION_CONFLICT",
    )
    if message.startswith(known):
        return ValueError(message)
    return ValueError(f"{fallback}: Google Drive operation failed.")


def export_project_to_drive(project: StoryProject, *, chapter: Chapter | None = None, export_id: str | None = None) -> WriterExport:
    ensure_project_writable(project)
    if chapter is not None and chapter.project_id != project.pk:
        raise ValueError("capítulo não pertence ao projeto")
    chapter = chapter or project.chapters.exclude(status=Chapter.Status.FINAL).order_by("number").first()
    if chapter is None:
        raise ValueError("Todos os capítulos já foram importados ou finalizados.")
    export_id = export_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    expected = [_expected_row(chapter, export_version=_next_export_version(project, chapter))]
    with transaction.atomic():
        export, _ = WriterExport.objects.select_for_update().get_or_create(
            project=project,
            export_id=export_id,
            defaults={"status": WriterExport.Status.CREATING},
        )
        export.expected_files = expected
        export.save(update_fields=("expected_files", "updated_at"))
    try:
        drive = _drive()
        root_id = project.drive_project_folder_id.strip()
        if root_id:
            root = drive.files().get(
                fileId=root_id,
                fields="id,name,mimeType,trashed,webViewLink",
            ).execute()
        else:
            configured_root = os.environ.get("GAIDEN_WRITER_DRIVE_ROOT_ID", "").strip() or None
            writer_root = _find_or_create_folder(drive, "Gaiden_Writer", configured_root)
            projects_root = _find_or_create_folder(drive, "Projects", writer_root["id"])
            root = _find_or_create_folder(drive, _slug(project.title), projects_root["id"])
            StoryProject.objects.filter(pk=project.pk, drive_project_folder_id="").update(
                drive_project_folder_id=root["id"],
            )
            project.drive_project_folder_id = root["id"]
        if root.get("trashed") or (root.get("mimeType") and root.get("mimeType") != GOOGLE_FOLDER):
            raise ValueError("registered project Google Drive folder is not available")
        exports_folder = _find_or_create_folder(drive, "exports", root["id"])
        export_folder = _find_or_create_folder(drive, f"chapter_{chapter.number:02d}_{export.export_id}", exports_folder["id"])
        returns_folder = _find_or_create_folder(drive, "return", root["id"])
        chapter_return_folder = _find_or_create_folder(drive, f"chapter_{chapter.number:02d}", returns_folder["id"])
        return_folder = _find_or_create_folder(drive, export.export_id, chapter_return_folder["id"])
        with transaction.atomic():
            export = WriterExport.objects.select_for_update().get(pk=export.pk)
            export.export_folder_id = export_folder["id"]
            export.export_folder_url = export_folder.get("webViewLink") or _drive_url(export_folder["id"])
            export.return_folder_id = return_folder["id"]
            export.return_folder_url = return_folder.get("webViewLink") or _drive_url(return_folder["id"])
            export.save(update_fields=("export_folder_id", "export_folder_url", "return_folder_id", "return_folder_url", "updated_at"))
        expected[0].update({
            "export_id": export.export_id,
            "export_timestamp": export.created_at.isoformat(),
            "drive_folder_id": export.export_folder_id,
            "return_folder_id": export.return_folder_id,
            "source_file_id": "",
            "state": "WAITING_RETURN",
        })
        app_props = {
            "project_id": str(project.pk), "chapter_id": str(chapter.pk),
            "export_id": export.export_id, "export_version": str(expected[0]["export_version"]),
        }
        contract_text = _global_contract(project)
        chapter_contract = "\n\n".join((contract_text.strip(), _chapter_contract(project, chapter, export))).strip() + "\n"
        request_file = _create_text_file(
            drive,
            export_folder["id"],
            f"chapter_{chapter.number:02d}_request.md",
            chapter_contract,
            app_properties={**app_props, "chapter_number": str(chapter.number), "expected_filename": _expected_filename(chapter)},
        )
        expected[0]["source_file_id"] = request_file["id"]
        contract_payload = _manifest(project, export, expected)
        contract_file = _create_text_file(
            drive, export_folder["id"], "export_contract.json",
            json.dumps(contract_payload, ensure_ascii=False, indent=2) + "\n",
            app_properties=app_props, mime_type="application/json",
        )
        with transaction.atomic():
            export = WriterExport.objects.select_for_update().get(pk=export.pk)
            export.contract_file_id = request_file["id"]
            export.contract_file_url = request_file.get("webViewLink") or _file_url(request_file["id"])
            export.expected_files = expected
            export.clipboard_contract = chapter_contract
            manifest = _manifest(project, export, expected)
            manifest["contract_json_file_id"] = contract_file["id"]
            manifest["contract_json_file_url"] = contract_file.get("webViewLink") or _file_url(contract_file["id"])
            export.manifest_json = manifest
            export.save(update_fields=("contract_file_id", "contract_file_url", "expected_files", "clipboard_contract", "manifest_json", "updated_at"))
        with transaction.atomic():
            export = WriterExport.objects.select_for_update().get(pk=export.pk)
            export.status = WriterExport.Status.READY if export.return_folder_id else WriterExport.Status.FAILED
            export.error_message = "" if export.return_folder_id else "return_folder_id ausente"
            export.ready_at = django_timezone.now() if export.return_folder_id else None
            export.save(update_fields=("status", "error_message", "ready_at", "updated_at"))
        return export
    except Exception as exc:
        export.refresh_from_db()
        _set_export_state(export, "EXPORT_ERROR")
        error = _drive_error(exc, fallback="EXPORT_FAILED", missing="FOLDER_NOT_FOUND")
        WriterExport.objects.filter(pk=export.pk).update(status=WriterExport.Status.FAILED, error_message=str(error)[:2000])
        raise error from exc


def _clipboard_contract(project: StoryProject, export: WriterExport, expected: list[dict], contract_text: str) -> str:
    return "\n\n".join([
        "GAIDEN WRITER EXTERNAL PRODUCTION PACKAGE",
        f"Project: {project.title}",
        f"Export ID: {export.export_id}",
        f"Export folder: {export.export_folder_url}",
        f"Return folder: {export.return_folder_url}",
        "Expected files:",
        "\n".join(f"- {row['filename']} ({row['target_words']} words)" for row in expected),
        "After generating each chapter, save the completed manuscript in the Google Drive return folder identified above. Do not save it in the project root, chapters folder, or another export.",
        contract_text,
    ]).strip() + "\n"


def _download_text(drive, file_id: str) -> str:
    from googleapiclient.http import MediaIoBaseDownload
    import io

    request = drive.files().get_media(fileId=file_id)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request, chunksize=1024 * 1024)
    for _ in range(64):
        _, done = downloader.next_chunk(num_retries=0)
        if done:
            return buffer.getvalue().decode("utf-8", errors="strict")
    raise ValueError("download do retorno não concluiu")


def scan_return_files(export: WriterExport) -> list[ReturnFile]:
    if export.status != WriterExport.Status.READY or not export.return_folder_id:
        raise ValueError("exportação não possui return/ pronto")
    drive = _drive()
    result = drive.files().list(
        q=f"'{export.return_folder_id}' in parents and trashed = false and mimeType != '{GOOGLE_FOLDER}'",
        fields="nextPageToken,files(id,name,mimeType,size,md5Checksum,appProperties,webViewLink)",
        pageSize=200,
        orderBy="name",
    ).execute()
    if result.get("nextPageToken"):
        raise ValueError("IMPORT_FAILED: pasta return contém arquivos demais para uma verificação completa")
    files = result.get("files", [])
    by_name = {}
    for item in files:
        if item["name"] in by_name:
            raise ValueError(f"IMPORT_FAILED: arquivo duplicado na pasta return: {item['name']}")
        by_name[item["name"]] = item
    chapters = {str(chapter.pk): chapter for chapter in export.project.chapters.all()}
    rows = []
    for expected in export.expected_files:
        if expected.get("return_folder_id") and expected["return_folder_id"] != export.return_folder_id:
            raise ValueError("CHAPTER_MISMATCH: pasta return não corresponde ao registro da exportação")
        if expected.get("export_id") and expected["export_id"] != export.export_id:
            raise ValueError("CHAPTER_MISMATCH: export_id não corresponde ao registro da exportação")
        item = by_name.get(expected["filename"])
        if not item:
            raise ValueError(f"FILE_NOT_FOUND: {expected['filename']} não foi encontrado na pasta return desta exportação")
        metadata_item = by_name.get(expected["metadata_filename"])
        identity = {
            "project_id": str(export.project_id),
            "chapter_id": str(expected["chapter_id"]),
            "export_id": export.export_id,
            "export_version": int(expected["export_version"]),
        }
        for drive_item in (item, metadata_item):
            if not drive_item:
                continue
            for key, value in (drive_item.get("appProperties") or {}).items():
                if key in identity and str(value) != str(identity[key]):
                    code = "VERSION_CONFLICT" if key == "export_version" else "CHAPTER_MISMATCH"
                    raise ValueError(f"{code}: identidade do arquivo no Drive não corresponde ao contrato {export.export_id}")
        if metadata_item:
            try:
                metadata = json.loads(_download_text(drive, metadata_item["id"]))
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"metadados de retorno inválidos: {expected['metadata_filename']}") from exc
            if not isinstance(metadata, dict):
                raise ValueError(f"metadados de retorno inválidos: {expected['metadata_filename']}")
            metadata["identity_source"] = "response_json"
        else:
            other_responses = [name for name in by_name if name.endswith("_response.md") and name != expected["filename"]]
            if other_responses:
                raise ValueError("CHAPTER_MISMATCH: pasta return contém resposta de outro capítulo")
            metadata = {
                **identity,
                "contract_version": expected.get("contract_version", CONTRACT_VERSION),
                "status": "COMPLETED",
                "content_file": expected["filename"],
                "identity_source": "linked_return_folder",
            }
        received = {
            "project_id": str(metadata.get("project_id", "")),
            "chapter_id": str(metadata.get("chapter_id", "")),
            "export_id": str(metadata.get("export_id", "")),
            "export_version": int(metadata.get("export_version", 0) or 0),
        }
        if received["export_version"] != identity["export_version"]:
            _set_export_state(export, "STALE_VERSION")
            raise ValueError(f"VERSION_CONFLICT: versão do retorno não corresponde ao contrato {export.export_id}")
        if received != identity:
            _set_export_state(export, "MISMATCH")
            raise ValueError(f"CHAPTER_MISMATCH: identidade do retorno não corresponde ao contrato {export.export_id}")
        if str(metadata.get("status", "")).strip().lower() not in ("completed", "ready"):
            raise ValueError("retorno ainda não está marcado como concluído")
        content_name = metadata.get("content_file")
        if content_name != expected["filename"]:
            raise ValueError("MISMATCH: content_file não corresponde ao contrato")
        chapter = chapters.get(str(expected["chapter_id"]))
        if not chapter:
            raise ValueError(f"capítulo ausente no projeto: {expected['chapter_id']}")
        if chapter.project_id != export.project_id:
            raise ValueError("MISMATCH: capítulo não pertence ao projeto")
        downloaded_text = _download_text(drive, item["id"])
        text = downloaded_text.strip()
        if not text:
            raise ValueError(f"{expected['filename']} está vazio")
        declared_digest = str(metadata.get("sha256", "")).strip().lower()
        downloaded_digest = hashlib.sha256(downloaded_text.encode("utf-8")).hexdigest()
        if declared_digest and declared_digest != downloaded_digest:
            raise ValueError(f"IMPORT_FAILED: integridade inválida para {expected['filename']}")
        if not metadata_item:
            metadata["sha256"] = downloaded_digest
            metadata["word_count"] = len(text.split())
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        rows.append(ReturnFile(
            chapter=chapter,
            expected=expected,
            drive_file=item,
            text=text,
            sha256=digest,
            word_count=len(text.split()),
            duplicate=bool(chapter.generated_text.strip() and hashlib.sha256(chapter.generated_text.strip().encode("utf-8")).hexdigest() == digest),
            metadata=metadata,
        ))
    if rows:
        _set_export_state(export, "VALIDATED")
    return rows


def _import_return_files(export: WriterExport) -> dict:
    ensure_project_writable(export.project)
    imported = skipped = 0
    files = scan_return_files(export)
    with transaction.atomic():
        for item in files:
            chapter = Chapter.objects.select_for_update().get(pk=item.chapter.pk)
            current_digest = hashlib.sha256(chapter.generated_text.strip().encode("utf-8")).hexdigest() if chapter.generated_text.strip() else ""
            if current_digest == item.sha256:
                already_imported = chapter.generation_metadata.get("writer_export_id") == export.export_id
                _set_export_state(export, "IMPORTED" if already_imported else "DUPLICATE")
                skipped += 1
                continue
            if chapter.status == Chapter.Status.FINAL or chapter.final_text.strip():
                raise ValueError(f"capítulo {chapter.number:02d} já foi aprovado e não pode ser sobrescrito")
            chapter.generated_text = item.text
            chapter.generated_at = django_timezone.now()
            chapter.generation_model = "external-return"
            chapter.generation_metadata = {
                "writer_export_id": export.export_id,
                "export_version": item.expected["export_version"],
                "contract_version": item.expected["contract_version"],
                "return_file_id": item.drive_file["id"],
                "return_filename": item.expected["filename"],
                "word_count": item.word_count,
                "sha256": item.sha256,
                "identity_source": item.metadata["identity_source"],
                "technical_status": "imported",
                "editorial_status": "review_required",
            }
            chapter.status = Chapter.Status.OUTPUT_IMPORTED
            chapter.error_message = ""
            chapter.save(update_fields=("generated_text", "generated_at", "generation_model", "generation_metadata", "status", "error_message", "updated_at"))
            imported += 1
            manifest = dict(export.manifest_json)
            history = list(manifest.get("imports", []))
            history.append({
                "chapter_id": str(chapter.pk), "export_id": export.export_id,
                "export_version": item.expected["export_version"], "return_file_id": item.drive_file["id"],
                "sha256": item.sha256, "word_count": item.word_count,
                "identity_source": item.metadata["identity_source"],
                "imported_at": chapter.generated_at.isoformat(), "content": item.text,
            })
            manifest["imports"] = history
            export.manifest_json = manifest
            export.expected_files = [{**row, "state": "IMPORTED"} if str(row.get("chapter_id")) == str(chapter.pk) else row for row in export.expected_files]
            export.error_message = ""
            export.save(update_fields=("manifest_json", "expected_files", "error_message", "updated_at"))
    return {"found": len(files), "imported": imported, "skipped": skipped}


def import_return_files(export: WriterExport) -> dict:
    try:
        return _import_return_files(export)
    except Exception as exc:
        export.refresh_from_db()
        current_state = export.expected_files[0].get("state") if export.expected_files else ""
        if current_state not in {"MISMATCH", "STALE_VERSION", "DUPLICATE"}:
            _set_export_state(export, "IMPORT_ERROR")
        if str(exc).startswith("FILE_NOT_FOUND:"):
            export.error_message = str(exc)[:2000]
            export.save(update_fields=("error_message", "updated_at"))
        if str(exc).startswith(("CHAPTER_MISMATCH", "VERSION_CONFLICT", "DUPLICATE")):
            raise
        raise _drive_error(exc, fallback="IMPORT_FAILED") from exc
