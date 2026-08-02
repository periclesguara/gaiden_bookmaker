from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from django.conf import settings

from pipeline.services.incremental_export import RclonePublisher


BOOK_CODE_RE = re.compile(r"^book_[0-9]+$")
TARGET_FOLDERS = {"en_us": "en-us", "ptbr": "pt-br", "fr": "fr"}
RETURN_EXTENSIONS = {".txt", ".md"}


def drive_target_folder(target_language: str) -> str:
    value = (target_language or "").strip().lower().replace("-", "_")
    if value not in TARGET_FOLDERS:
        raise ValueError("Idioma de tradução não permitido.")
    return TARGET_FOLDERS[value]


def drive_job_path(book_code: str, target_language: str, *, remote: str | None = None) -> str:
    if not BOOK_CODE_RE.fullmatch(book_code or ""):
        raise ValueError("Código do livro inválido para o job de tradução.")
    remote_name = (remote or settings.GAIDEN_DRIVE_REMOTE).strip().rstrip(":")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", remote_name):
        raise ValueError("Remote rclone inválido.")
    return f"{remote_name}:04_TRANSLATION_JOBS/{book_code}/{drive_target_folder(target_language)}"


def export_job(
    *,
    book_code: str,
    title: str,
    author: str,
    source_language: str,
    target_language: str,
    source_path: Path,
    publisher=None,
) -> dict[str, str | int]:
    if not source_path.is_file():
        raise FileNotFoundError(f"Heading Cleaner não encontrado: {source_path}")
    data = source_path.read_bytes()
    if not data.strip():
        raise ValueError("O resultado do Heading Cleaner está vazio.")
    source_sha256 = hashlib.sha256(data).hexdigest()
    destination = drive_job_path(book_code, target_language)
    expected_name = f"{book_code}_{drive_target_folder(target_language).replace('-', '_')}_translated.txt"
    input_name = f"{book_code}_heading_clean.txt"
    contract = {
        "schema": "gaiden_manual_translation_job_v1",
        "book_code": book_code,
        "title": title,
        "author": author,
        "source_language": source_language,
        "target_language": target_language,
        "source": {"file": f"input/{input_name}", "sha256": source_sha256, "size_bytes": len(data)},
        "return": {"directory": "return", "expected_file": expected_name},
        "instructions": [
            "Traduza ou modernize o texto integralmente.",
            "Preserve títulos, ordem, parágrafos e separadores.",
            "Não resuma e não remova conteúdo.",
            f"Grave o resultado em return/{expected_name}.",
        ],
    }
    active_publisher = publisher or RclonePublisher(destination)
    active_publisher.publish_bytes(f"input/{input_name}", data)
    active_publisher.publish_bytes(
        "input/translation-job.json",
        (json.dumps(contract, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    active_publisher.publish_bytes(
        "return/RETURN_HERE.txt",
        f"Coloque a tradução concluída nesta pasta com o nome {expected_name}.\n".encode("utf-8"),
    )
    return {
        "drive_path": destination,
        "source_path": str(source_path),
        "source_sha256": source_sha256,
        "expected_return_name": expected_name,
        "size_bytes": len(data),
    }


def read_drive_return(drive_path: str) -> dict[str, object]:
    destination = RclonePublisher(drive_path).destination
    return_dir = f"{destination}/return"
    listed = subprocess.run(
        ["rclone", "lsjson", return_dir, "--files-only"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if listed.returncode:
        detail = listed.stderr.decode("utf-8", errors="replace").strip()
        raise OSError(f"Não foi possível ler o retorno do Drive: {detail}")
    rows = json.loads(listed.stdout or b"[]")
    candidates = [
        row for row in rows
        if Path(str(row.get("Name") or row.get("Path") or "")).suffix.lower() in RETURN_EXTENSIONS
        and str(row.get("Name") or row.get("Path") or "") != "RETURN_HERE.txt"
    ]
    if not candidates:
        raise FileNotFoundError("Nenhum arquivo traduzido foi encontrado na subpasta return.")
    candidates.sort(key=lambda row: (str(row.get("ModTime") or ""), str(row.get("Name") or "")), reverse=True)
    selected = candidates[0]
    name = str(selected.get("Name") or selected.get("Path"))
    fetched = subprocess.run(
        ["rclone", "cat", f"{return_dir}/{name}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if fetched.returncode:
        detail = fetched.stderr.decode("utf-8", errors="replace").strip()
        raise OSError(f"Não foi possível baixar o retorno do Drive: {detail}")
    return {"name": name, "data": fetched.stdout, "remote_path": f"{return_dir}/{name}"}
