from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from writer.models import Chapter, StoryProject

MANIFEST_NAME = "WRITER.HANDOFF.json"
BODY_NAME = "body.md"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=".handoff-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def merged_project_body(project: StoryProject) -> str:
    chapters = list(project.chapters.order_by("number"))
    if not chapters:
        raise ValueError("the project has no chapters")
    if len(chapters) != project.chapter_count:
        raise ValueError("the project chapter table is incomplete")
    pending = [chapter.number for chapter in chapters if chapter.status != Chapter.Status.FINAL]
    if pending:
        numbers = ", ".join(f"{number:02d}" for number in pending)
        raise ValueError(f"finalize every chapter before handoff; pending: {numbers}")
    sections = [f"# {project.title.strip()}"]
    for chapter in chapters:
        heading = f"## Chapter {chapter.number:02d}"
        if chapter.title.strip():
            heading += f" — {chapter.title.strip()}"
        sections.append(f"{heading}\n\n{chapter.final_text.strip()}")
    return "\n\n".join(sections).strip() + "\n"


def export_project_handoff(project: StoryProject) -> Path:
    body = merged_project_body(project).encode("utf-8")
    body_sha = _sha256(body)
    root = Path(settings.WRITER_HANDOFF_ROOT).expanduser().resolve()
    destination = root / f"project-{project.id:06d}" / "outbound"
    body_path = destination / BODY_NAME
    manifest_path = destination / MANIFEST_NAME

    if manifest_path.exists() or body_path.exists():
        if not manifest_path.is_file() or not body_path.is_file():
            raise ValueError("partial handoff exists; review it before retrying")
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if _sha256(body_path.read_bytes()) != body_sha:
            raise ValueError("handoff already exists with different body content")
        if existing.get("body", {}).get("sha256") != body_sha:
            raise ValueError("existing handoff manifest does not match its body")
        return destination

    manifest = {
        "contract_version": 1,
        "handoff_id": f"writer-project-{project.id:06d}-{body_sha[:12]}",
        "source_system": "writer",
        "destination_system": "gaiden_bookmaker",
        "status": "AWAITING_GPT_PLUS_WORK",
        "project": {
            "writer_project_id": project.id,
            "title": project.title,
            "language": project.language,
            "writing_mode": project.writing_mode,
        },
        "body": {
            "file": BODY_NAME,
            "media_type": "text/markdown",
            "sha256": body_sha,
            "bytes": len(body),
        },
        "next_step": {
            "processor": "GPT_PLUS_WORK",
            "return_status": "GAIDEN_BODY_READY",
            "gaiden_entry_stage": "FRONTMATTER_ASSETS",
            "skip_stages": ["BLOCK_01"],
        },
        "created_at": timezone.now().isoformat(),
    }
    _atomic_write(body_path, body)
    _atomic_write(
        manifest_path,
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return destination
