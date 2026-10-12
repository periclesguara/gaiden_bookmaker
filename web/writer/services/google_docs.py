from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .source_loader import RawDocument

_DOC_ID = re.compile(r"^[A-Za-z0-9_-]{10,}$")
_DOC_URL = re.compile(r"https?://docs\.google\.com/document/d/([A-Za-z0-9_-]+)(?:/(?:edit)?)?(?:\?.*)?$")


def extract_document_id(value: str) -> str:
    candidate = value.strip()
    match = _DOC_URL.fullmatch(candidate) or _DOC_URL.match(candidate)
    document_id = match.group(1) if match else candidate
    if not _DOC_ID.fullmatch(document_id):
        raise ValueError("Informe uma URL válida do Google Docs ou um documentId válido.")
    return document_id


def canonical_google_doc_url(document_id: str) -> str:
    return f"https://docs.google.com/document/d/{extract_document_id(document_id)}/edit"


def _credentials():
    import os
    import shutil
    import subprocess

    import google.auth
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials

    scopes = [
        "https://www.googleapis.com/auth/drive",
    ]

    path = (
        os.environ.get("GAIDEN_GOOGLE_CREDENTIALS_FILE")
        or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    )

    if path:
        try:
            return service_account.Credentials.from_service_account_file(
                path,
                scopes=scopes,
            )
        except Exception as exc:
            raise RuntimeError("AUTH_REQUIRED: configured Google credentials are unavailable.") from exc

    gcloud = shutil.which("gcloud")

    if gcloud:
        result = subprocess.run(
            [gcloud, "auth", "print-access-token"],
            capture_output=True,
            text=True,
            check=False,
        )

        token = result.stdout.strip()

        if result.returncode == 0 and token:
            return Credentials(token=token)

        stderr = result.stderr.lower()
        if any(marker in stderr for marker in ("expired", "reauth", "invalid_grant")):
            raise RuntimeError("TOKEN_EXPIRED: renew the existing Google CLI login.")

    try:
        credentials, _ = google.auth.default(scopes=scopes)
        if credentials is not None:
            return credentials
    except Exception:
        pass

    raise RuntimeError(
        "AUTH_REQUIRED: Google Drive authorization is required. "
        "Run the supported Google CLI login with Drive access."
    )


def _paragraph_text(element: dict) -> tuple[str, str]:
    paragraph = element.get("paragraph", {})
    style = paragraph.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
    text = "".join(
        item.get("textRun", {}).get("content", "")
        for item in paragraph.get("elements", [])
    ).rstrip("\n")
    return style, text


def _to_text(body: dict) -> tuple[str, list[dict], list[str]]:
    lines, structure, unsupported = [], [], []
    for element in body.get("content", []):
        if "paragraph" in element:
            style, text = _paragraph_text(element)
            if style == "TITLE":
                text = f"# {text}" if text else text
            elif style == "HEADING_1":
                text = f"# {text}" if text else text
            elif style == "HEADING_2":
                text = f"## {text}" if text else text
            elif style == "HEADING_3":
                text = f"### {text}" if text else text
            lines.append(text)
            structure.append({"style": style, "text": text})
        elif "sectionBreak" not in element:
            unsupported.append(next(iter(element), "unknown"))
    return "\n\n".join(lines).strip() + "\n", structure, unsupported


def load_google_doc(document) -> RawDocument:
    from googleapiclient.discovery import build

    document_id = extract_document_id(document.external_id or document.external_url)
    credentials = _credentials()
    docs = build("docs", "v1", credentials=credentials, cache_discovery=False)
    payload = docs.documents().get(documentId=document_id).execute()
    text, structure, unsupported = _to_text(payload.get("body", {}).get("content", []))
    modified_at = None
    revision_id = payload.get("revisionId", "")
    try:
        drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
        metadata = drive.files().get(fileId=document_id, fields="name,modifiedTime,headRevisionId").execute()
        title = metadata.get("name", payload.get("title", document.filename))
        revision_id = metadata.get("headRevisionId", revision_id)
        if metadata.get("modifiedTime"):
            modified_at = datetime.fromisoformat(metadata["modifiedTime"].replace("Z", "+00:00"))
    except Exception:
        title = payload.get("title", document.filename)
    return RawDocument(
        source_type="GOOGLE_DOC", text=text, title=title,
        external_id=document_id, external_url=canonical_google_doc_url(document_id),
        revision_id=revision_id, modified_at=modified_at, structure=structure,
        metadata={"unsupported_elements": unsupported},
    )
