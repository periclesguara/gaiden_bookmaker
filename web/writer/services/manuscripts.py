from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import io
import zipfile
from pathlib import Path

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from gaiden.application.pipeline.ingest import extract_text_from_file

from ..models import ManuscriptRevision, StoryProject
from .normalization import writer_storage_root

MAX_MANUSCRIPT_BYTES = 50 * 1024 * 1024
ALLOWED_SUFFIXES = {".md", ".txt", ".docx"}
CHAPTER_HEADING = re.compile(
    r"^\s{0,3}(?:#{1,6}\s*)?CHAPTER\s+"
    r"(\d+|[IVXLCDM]+|ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN)\b",
    re.IGNORECASE,
)
CHAPTER_WORDS = {
    "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
    "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10,
}


class ProjectClosedError(PermissionDenied):
    """Raised when a write is attempted after Writer closure."""


def ensure_project_writable(project: StoryProject) -> None:
    if getattr(project, "status", StoryProject.Status.WRITING) == StoryProject.Status.CLOSED:
        raise ProjectClosedError("projeto fechado; o Writer está somente leitura")


def _uploaded_bytes(uploaded_file) -> bytes:
    size = getattr(uploaded_file, "size", None)
    if size is not None and size > MAX_MANUSCRIPT_BYTES:
        raise ValueError("manuscrito excede o limite de 50 MiB")
    chunks: list[bytes] = []
    total = 0
    for chunk in uploaded_file.chunks():
        total += len(chunk)
        if total > MAX_MANUSCRIPT_BYTES:
            raise ValueError("manuscrito excede o limite de 50 MiB")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise ValueError("o manuscrito não pode estar vazio")
    return data


def _technical_normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if "\x00" in text:
        raise ValueError("manuscrito contém bytes NUL")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.rstrip() + "\n"


def _read_text(data: bytes, suffix: str) -> str:
    if suffix in {".md", ".txt"}:
        try:
            return _technical_normalize(data.decode("utf-8", errors="strict"))
        except UnicodeDecodeError as exc:
            raise ValueError("o manuscrito precisa estar em UTF-8") from exc
    # Bound decompression before handing DOCX to the existing paragraph reader.
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > 5000 or sum(item.file_size for item in members) > 100 * 1024 * 1024:
                raise ValueError("DOCX excede o limite de conteúdo descompactado")
    except zipfile.BadZipFile as exc:
        raise ValueError("arquivo DOCX inválido") from exc
    with tempfile.NamedTemporaryFile(suffix=suffix) as temporary:
        temporary.write(data)
        temporary.flush()
        extracted = extract_text_from_file(Path(temporary.name), suffix.lstrip("."))
    if not extracted:
        raise ValueError("não foi possível extrair texto do DOCX")
    return _technical_normalize(extracted)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=".manuscript-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _immutable_write(path: Path, content: bytes) -> None:
    """Publish complete bytes atomically without replacing an operator file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".revision-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ValueError("o caminho já existe; nenhuma versão foi sobrescrita") from exc
    finally:
        Path(temporary).unlink(missing_ok=True)


def revision_bytes(project, revision) -> bytes:
    if revision.project_id != project.pk:
        raise ValueError("a revisão não pertence a este projeto")
    path = Path(revision.storage_path).expanduser()
    root = writer_storage_root().resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError("arquivo de manuscrito indisponível ou fora do storage Writer")
    if path.stat().st_size > MAX_MANUSCRIPT_BYTES:
        raise ValueError("manuscrito excede o limite de 50 MiB")
    data = path.read_bytes()
    if not data or hashlib.sha256(data).hexdigest() != revision.sha256:
        raise ValueError("o hash do manuscrito não confere")
    return data


def _revision_path(project: StoryProject, version: int) -> Path:
    return writer_storage_root() / "projects" / str(project.pk) / "manuscripts" / f"manuscript_v{version:03d}.md"


def _chapter_identity(token: str) -> str:
    normalized = token.upper()
    if normalized.isdigit():
        return str(int(normalized))
    if normalized in CHAPTER_WORDS:
        return str(CHAPTER_WORDS[normalized])
    return f"roman:{normalized}"


def detect_chapter_headings(text: str) -> list[str]:
    """Return first-seen chapter headings, deduplicated by chapter number."""
    headings: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        match = CHAPTER_HEADING.match(line)
        if not match:
            continue
        identity = _chapter_identity(match.group(1))
        if identity not in seen:
            seen.add(identity)
            headings.append(line)
    return headings


@transaction.atomic
def import_manuscript_revision(project: StoryProject, uploaded_file, created_by=None) -> ManuscriptRevision:
    ensure_project_writable(project)
    project = StoryProject.objects.select_for_update().get(pk=project.pk)
    ensure_project_writable(project)
    original_filename = Path(getattr(uploaded_file, "name", "manuscript.md")).name[:255]
    suffix = Path(original_filename).suffix.casefold()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError("formato não suportado; use .md, .txt ou .docx")
    data = _uploaded_bytes(uploaded_file)
    text = _read_text(data, suffix)
    if not text.strip():
        raise ValueError("o manuscrito precisa conter texto")
    encoded = text.encode("utf-8")
    version = (ManuscriptRevision.objects.filter(project=project).order_by("-version").values_list("version", flat=True).first() or 0) + 1
    path = _revision_path(project, version)
    if path.exists():
        raise ValueError("o próximo caminho de manuscrito já existe; verifique o storage")
    _immutable_write(path, encoded)
    detected = len(detect_chapter_headings(text))
    try:
        return ManuscriptRevision.objects.create(
            project=project,
            version=version,
            source_kind=ManuscriptRevision.SourceKind.IMPORTED_FINAL,
            original_filename=original_filename,
            storage_path=str(path),
            sha256=hashlib.sha256(encoded).hexdigest(),
            word_count=len(text.split()),
            character_count=len(text),
            detected_chapter_count=detected,
            created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
        )
    except BaseException:
        path.unlink(missing_ok=True)
        raise


@transaction.atomic
def promote_canonical_manuscript(project: StoryProject, revision: ManuscriptRevision) -> StoryProject:
    ensure_project_writable(project)
    project = StoryProject.objects.select_for_update().get(pk=project.pk)
    ensure_project_writable(project)
    if revision.project_id != project.pk:
        raise ValueError("a revisão não pertence a este projeto")
    revision_bytes(project, revision)
    current = project.canonical_manuscript
    if (current and current.source_kind == ManuscriptRevision.SourceKind.IMPORTED_FINAL
            and revision.source_kind != ManuscriptRevision.SourceKind.IMPORTED_FINAL):
        raise ValueError("uma Final Version salva tem prioridade sobre o merge dos capítulos")
    project.canonical_manuscript = revision
    project.status = StoryProject.Status.CANONICAL_READY
    project.save(update_fields=("canonical_manuscript", "status", "updated_at"))
    return project


@transaction.atomic
def close_writer_project(project: StoryProject, closed_by=None) -> StoryProject:
    # Lock only the project row. PostgreSQL rejects FOR UPDATE on the nullable
    # side of the outer join produced by select_related("canonical_manuscript").
    project = StoryProject.objects.select_for_update().get(pk=project.pk)
    ensure_project_writable(project)
    revision = project.canonical_manuscript
    if revision is None or revision.project_id != project.pk:
        raise ValueError("selecione um manuscrito canônico antes de fechar")
    path = Path(revision.storage_path)
    if not path.is_file():
        raise ValueError("o arquivo do manuscrito canônico não existe no storage")
    if path.stat().st_size == 0:
        raise ValueError("o arquivo do manuscrito canônico está vazio")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != revision.sha256:
        raise ValueError("o hash do manuscrito canônico não confere")
    if revision.word_count <= 0:
        raise ValueError("o manuscrito canônico precisa conter palavras")
    closed_at = timezone.now()
    manifest = {
        "schema_version": 1,
        "project_id": project.pk,
        "title": project.title,
        "language": project.language,
        "writing_mode": getattr(project, "writing_mode", ""),
        "canonical_revision_id": revision.pk,
        "canonical_version": revision.version,
        "canonical_path": revision.storage_path,
        "sha256": revision.sha256,
        "word_count": revision.word_count,
        "character_count": revision.character_count,
        "detected_chapter_count": revision.detected_chapter_count,
        "closed_at": closed_at.isoformat(),
    }
    manifest_path = path.parent / "manifest.json"
    previous_manifest = manifest_path.read_bytes() if manifest_path.exists() else None
    _atomic_write(
        manifest_path,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    project.status = StoryProject.Status.CLOSED
    project.closed_at = closed_at
    project.closed_by = closed_by if getattr(closed_by, "is_authenticated", False) else None
    try:
        project.save(update_fields=("status", "closed_at", "closed_by", "updated_at"))
    except BaseException:
        if previous_manifest is None:
            manifest_path.unlink(missing_ok=True)
        else:
            _atomic_write(manifest_path, previous_manifest)
        raise
    return project
