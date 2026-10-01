"""Explicit, one-way handoff from a closed Writer project to Intake."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from editorial.models import Contributor, ContributorRole, Edition, EditionPipeline, Language, PipelineStage, Seal, Work
from gaiden.infrastructure import storage
from pipeline.models import BookEditionTemplate, IntakeCounter


class WriterIntakeHandoffError(ValueError):
    """Raised when the closed Writer canonical cannot safely enter Intake."""


@dataclass(frozen=True)
class WriterIntakeHandoffResult:
    edition: Edition
    created: bool
    working_path: Path
    working_sha256: str


@transaction.atomic
def handoff_writer_edition_to_intake(*, edition: Edition) -> str:
    """Register a closed Writer Edition in the Intake asset lane.

    The Intake number is operational metadata only; the canonical Work code,
    Edition, manuscript path and manuscript bytes remain unchanged.
    """
    edition = Edition.objects.select_for_update().select_related("work", "language").get(pk=edition.pk)
    provenance = dict(edition.work.source_provenance or {})
    if provenance.get("origin") != "writer_canonical":
        raise WriterIntakeHandoffError("Somente uma Edition writer_canonical pode entrar nesta ponte.")
    final_path = Path(provenance.get("working_manuscript_path") or "")
    if not final_path.is_file():
        raise WriterIntakeHandoffError("BOOK.MD_FINAL do Writer não foi encontrado.")
    digest = _sha256(final_path)
    expected = provenance.get("working_manuscript_sha256") or provenance.get("source_sha256")
    if expected and digest != expected:
        raise WriterIntakeHandoffError("BOOK.MD_FINAL diverge do SHA canônico do Writer.")

    intake_code = str(provenance.get("intake_book_code") or "").strip()
    if not intake_code:
        counter, _ = IntakeCounter.objects.select_for_update().get_or_create(
            key="book", defaults={"next_value": 1}
        )
        intake_number = counter.next_value
        counter.next_value = intake_number + 1
        counter.save(update_fields=["next_value", "updated_at"])
        intake_code = f"book_{intake_number:04d}"

    template, _ = BookEditionTemplate.objects.select_for_update().get_or_create(
        book_code=edition.work.code,
        language=edition.language.code,
        defaults={
            "title": edition.title or edition.work.title,
            "author_name": edition.author or edition.work.author.name,
            "publication_year": edition.publication_year or 2026,
        },
    )
    template.text_source_mode = "txt"
    template.source_original_name = "BOOK.MD_FINAL"
    template.source_saved_path = str(final_path)
    template.source_file_size = final_path.stat().st_size
    template.source_file_sha256 = digest
    template.source_uploaded_by = "Writer canonical → Intake assets"
    template.source_uploaded_at = timezone.now()
    template.registration_status = BookEditionTemplate.STATUS_READY_FOR_BLOCK_02
    template.save()

    provenance.update(
        {
            "intake_book_code": intake_code,
            "intake_handoff": {
                "status": "ASSETS_READY",
                "source": "writer_canonical",
                "source_path": str(final_path),
                "source_sha256": digest,
                "handed_off_at": timezone.now().isoformat(),
            },
        }
    )
    edition.work.source_provenance = provenance
    edition.work.save(update_fields=["source_provenance"])
    return intake_code


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _writer_language_code(value: str) -> str:
    normalized = (value or "").strip().casefold()
    if normalized.startswith("english"):
        return "en"
    if normalized.startswith("portugu"):
        return "ptbr"
    if normalized.startswith("spanish") or normalized.startswith("espa"):
        return "es"
    if normalized.startswith("french") or normalized.startswith("fran"):
        return "fr"
    if normalized.startswith("german") or normalized.startswith("deutsch"):
        return "de"
    if normalized.startswith("italian") or normalized.startswith("ital"):
        return "it"
    raise WriterIntakeHandoffError(f"Idioma Writer sem mapeamento Intake: {value!r}.")


def _validate_closed_canonical(project) -> tuple[object, Path, dict]:
    from web.writer.models import StoryProject

    if project.status != StoryProject.Status.CLOSED:
        raise WriterIntakeHandoffError("O projeto Writer precisa estar CLOSED.")
    revision = project.canonical_manuscript
    if revision is None or revision.project_id != project.id:
        raise WriterIntakeHandoffError("O projeto Writer não possui revisão canônica válida.")

    canonical_path = Path(revision.storage_path)
    manifest_path = canonical_path.parent / "manifest.json"
    if not canonical_path.is_file() or not manifest_path.is_file():
        raise WriterIntakeHandoffError("Arquivo canônico ou manifest.json não encontrado.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WriterIntakeHandoffError("manifest.json do Writer é inválido.") from exc
    if not isinstance(manifest, dict):
        raise WriterIntakeHandoffError("manifest.json do Writer não contém um objeto.")

    digest = _sha256(canonical_path)
    if digest != revision.sha256 or manifest.get("sha256") != revision.sha256:
        raise WriterIntakeHandoffError("O SHA-256 físico, da revisão ou do manifesto não confere.")
    if manifest.get("project_id") != project.id:
        raise WriterIntakeHandoffError("manifest.json não pertence ao projeto Writer informado.")
    if manifest.get("canonical_revision_id") != revision.id:
        raise WriterIntakeHandoffError("manifest.json não aponta para a revisão canônica informada.")
    return revision, canonical_path, manifest


def _work_code(project) -> str:
    code = slugify(f"writer-{project.id}-{project.title}")[:50].strip("-")
    if not code:
        raise WriterIntakeHandoffError("Título insuficiente para criar o código da obra Intake.")
    return code


def _copy_canonical(source: Path, destination: Path, expected_sha256: str) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(file_descriptor, "wb") as target, source.open("rb") as origin:
            shutil.copyfileobj(origin, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        temporary_path = Path(temporary_name)
        copied_sha256 = _sha256(temporary_path)
        if copied_sha256 != expected_sha256:
            raise WriterIntakeHandoffError("A cópia Intake não preservou o SHA-256 canônico.")
        os.replace(temporary_path, destination)
        return copied_sha256
    finally:
        Path(temporary_name).unlink(missing_ok=True)


@transaction.atomic
def register_closed_writer_in_intake(*, project, author_name: str, publisher: str) -> WriterIntakeHandoffResult:
    """Create or reuse the post-editorial Intake Edition for a closed project.

    This operation never writes to Writer storage.  Its only manuscript write is
    a byte-for-byte copy to Intake's `BOOK.MD_FINAL` working artifact.
    """
    from web.writer.models import StoryProject

    author_name = (author_name or "").strip()
    publisher = (publisher or "").strip()
    if not author_name or not publisher:
        raise WriterIntakeHandoffError("Autor e publisher são obrigatórios para criar a Edition.")

    project = StoryProject.objects.select_for_update().get(pk=project.pk)
    revision, canonical_path, _manifest = _validate_closed_canonical(project)
    language_code = _writer_language_code(project.language)
    provenance_query = {
        "source_provenance__writer_project_id": project.id,
        "source_provenance__writer_revision_id": revision.id,
    }
    work = Work.objects.select_for_update().filter(**provenance_query).first()
    created = False

    if work is None:
        code = _work_code(project)
        conflicting = Work.objects.select_for_update().filter(code=code).first()
        if conflicting is not None:
            raise WriterIntakeHandoffError(
                f"O código Intake {code!r} já pertence a uma obra sem esta proveniência Writer."
            )
        language = Language.objects.filter(code=language_code).first()
        seal = Seal.objects.filter(name__iexact=publisher).order_by("id").first()
        if language is None or seal is None:
            raise WriterIntakeHandoffError("Idioma ou selo Intake obrigatório não está configurado.")
        author, _ = Contributor.objects.get_or_create(
            name=author_name,
            role=ContributorRole.AUTHOR,
        )
        work = Work.objects.create(
            code=code,
            title=project.title,
            original_language=language,
            author=author,
            publisher=publisher,
            year=timezone.now().year,
            is_public_domain=False,
            source_provenance={
                "origin": "writer_canonical",
                "writer_project_id": project.id,
                "writer_revision_id": revision.id,
                "source_sha256": revision.sha256,
            },
        )
        edition = Edition.objects.create(
            work=work,
            language=language,
            seal=seal,
            main_contributor=author,
            publisher=publisher,
            title=project.title,
            author=author_name,
            publication_year=timezone.now().year,
            imprint_name=publisher,
            seal_name=publisher,
        )
        BookEditionTemplate.objects.create(
            book_code=work.code,
            language=language_code,
            title=project.title,
            author_name=author_name,
            publication_year=timezone.now().year,
            work_kind=BookEditionTemplate.WORK_KIND_AUTHORIAL,
            imprint_name=publisher,
            seal_name=publisher,
            text_source_mode="txt",
            registration_status=BookEditionTemplate.STATUS_READY_FOR_BLOCK_02,
        )
        created = True
    else:
        edition = Edition.objects.select_for_update().filter(
            work=work,
            language__code=language_code,
        ).first()
        if edition is None:
            raise WriterIntakeHandoffError("Work Writer existente não possui a Edition Intake esperada.")

    working_path = storage.builds_dir(work.code, language_code) / "BOOK.MD_FINAL"
    if working_path.exists():
        copied_sha256 = _sha256(working_path)
        if copied_sha256 != revision.sha256:
            raise WriterIntakeHandoffError("BOOK.MD_FINAL existente diverge do canônico Writer; nenhuma sobrescrita foi feita.")
    else:
        copied_sha256 = _copy_canonical(canonical_path, working_path, revision.sha256)

    provenance = dict(work.source_provenance or {})
    provenance.update(
        {
            "origin": "writer_canonical",
            "writer_project_id": project.id,
            "writer_revision_id": revision.id,
            "source_sha256": revision.sha256,
            "working_manuscript_path": str(working_path),
            "working_manuscript_sha256": copied_sha256,
        }
    )
    work.source_provenance = provenance
    work.save(update_fields=["source_provenance"])

    pipeline, _ = EditionPipeline.objects.get_or_create(
        edition=edition,
        defaults={
            "current_stage": PipelineStage.FINAL_MD,
            "md_language": language_code,
            "final_md_at": timezone.now(),
            "last_log": f"WRITER_CANONICAL_IMPORTED :: project={project.id} revision={revision.id}",
        },
    )
    if pipeline.current_stage == PipelineStage.RAW:
        pipeline.current_stage = PipelineStage.FINAL_MD
        pipeline.md_language = language_code
        pipeline.final_md_at = timezone.now()
        pipeline.last_log = f"WRITER_CANONICAL_IMPORTED :: project={project.id} revision={revision.id}"
        pipeline.save(update_fields=["current_stage", "md_language", "final_md_at", "last_log"])

    return WriterIntakeHandoffResult(
        edition=edition,
        created=created,
        working_path=working_path,
        working_sha256=copied_sha256,
    )
