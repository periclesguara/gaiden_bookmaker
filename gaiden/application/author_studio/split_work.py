from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from gaiden.application.author_studio.tokenization import (
    DEFAULT_TOKENIZER_NAME,
    count_tokens,
    normalize_language,
    split_by_token_limit,
)
from gaiden.chunker import Chunk, make_chunks_from_text
from gaiden.domain.author_studio.enums import SplitOutcome, SplitRunStatus, SplitStatus

logger = logging.getLogger(__name__)

CHUNKER_VERSION = "author-studio-chunker-v3"
DEFAULT_MIN_TOKENS = 400
DEFAULT_TARGET_TOKENS = 700
DEFAULT_MAX_TOKENS = 900
DEFAULT_OVERLAP_TOKENS = 0


class CanonicalChangedDuringSplit(RuntimeError):
    pass


@dataclass(frozen=True)
class SplitResult:
    work_code: str
    chunk_count: int
    status: str
    outcome: str
    chunks_created: int = 0
    chunks_updated: int = 0
    chunks_preserved: int = 0
    chunks_removed: int = 0


@dataclass(frozen=True)
class SplitConfiguration:
    chunker_version: str = CHUNKER_VERSION
    tokenizer_name: str = DEFAULT_TOKENIZER_NAME
    minimum_tokens: int = DEFAULT_MIN_TOKENS
    target_tokens: int = DEFAULT_TARGET_TOKENS
    maximum_tokens: int = DEFAULT_MAX_TOKENS
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_canonical(canonical) -> tuple[str, str]:
    with canonical.text_file.open("rb") as handle:
        payload = handle.read()
    return payload.decode("utf-8"), _sha256(payload)


def _file_is_valid(chunk) -> bool:
    if not chunk.text_file or not chunk.text_file.storage.exists(chunk.text_file.name):
        return False
    with chunk.text_file.storage.open(chunk.text_file.name, "rb") as handle:
        return _sha256(handle.read()) == chunk.sha256


def _state_is_current(*, work, canonical, configuration: SplitConfiguration) -> bool:
    from author_studio.models import WorkChunk, WorkSplit

    state = WorkSplit.objects.filter(work=work).first()
    if not state or state.status != SplitStatus.COMPLETED.value:
        return False
    expected = {
        "source_sha256": canonical.sha256,
        "chunker_version": configuration.chunker_version,
        "tokenizer_name": configuration.tokenizer_name,
        "minimum_tokens": configuration.minimum_tokens,
        "target_tokens": configuration.target_tokens,
        "maximum_tokens": configuration.maximum_tokens,
        "overlap_tokens": configuration.overlap_tokens,
    }
    if any(getattr(state, field) != value for field, value in expected.items()):
        return False
    chunks = list(WorkChunk.objects.filter(work=work).order_by("sequence"))
    if not chunks or state.chunk_count != len(chunks):
        return False
    if [chunk.sequence for chunk in chunks] != list(range(1, len(chunks) + 1)):
        return False
    for chunk in chunks:
        if (
            chunk.canonical_text_id != canonical.pk
            or chunk.chunker_version != configuration.chunker_version
            or chunk.tokenizer_name != configuration.tokenizer_name
            or chunk.token_count < 1
            or chunk.token_count > configuration.maximum_tokens
            or not _file_is_valid(chunk)
        ):
            return False
    return True


def _validate_candidates(chunks: list[Chunk], configuration: SplitConfiguration) -> None:
    if not chunks:
        raise ValueError("O splitter não produziu chunks para o texto canônico.")
    for sequence, chunk in enumerate(chunks, start=1):
        if chunk.idx != sequence:
            raise ValueError("A sequência produzida pelo splitter contém lacunas.")
        if not chunk.text.strip():
            raise ValueError("O splitter produziu um chunk vazio.")
        if chunk.token_count > configuration.maximum_tokens:
            raise ValueError(
                f"Chunk {sequence} possui {chunk.token_count} tokens; limite: {configuration.maximum_tokens}."
            )
        if chunk.start_line > chunk.end_line:
            raise ValueError(f"Rastreabilidade de linhas inválida no chunk {sequence}.")


def _delete_storage_files(storage, names: set[str]) -> None:
    for name in sorted(item for item in names if item):
        if storage.exists(name):
            storage.delete(name)


def _remove_unreferenced_work_files(storage, work, active_names: set[str]) -> None:
    root = str(
        PurePosixPath("author_studio/authors")
        / work.author.code
        / "works"
        / work.code
        / "chunks"
    )

    def visit(directory: str) -> None:
        try:
            directories, files = storage.listdir(directory)
        except (FileNotFoundError, NotImplementedError):
            return
        for filename in files:
            name = str(PurePosixPath(directory) / filename)
            if name not in active_names and storage.exists(name):
                storage.delete(name)
        for child in directories:
            visit(str(PurePosixPath(directory) / child))

    visit(root)


def _finish_failed_run(run, exc: Exception) -> None:
    from author_studio.models import WorkSplitRun

    WorkSplitRun.objects.filter(pk=run.pk).update(
        status=SplitRunStatus.FAILED.value,
        outcome=SplitOutcome.FAILED.value,
        completed_at=timezone.now(),
        error=str(exc)[:4000],
    )


def _build_chunks(text: str, language: str, configuration: SplitConfiguration) -> list[Chunk]:
    normalized_language = normalize_language(language)

    def real_count(value: str) -> int:
        result = count_tokens(value, normalized_language)
        if result.tokenizer_name != configuration.tokenizer_name:
            raise RuntimeError(
                f"Tokenizer ativo {result.tokenizer_name} difere do configurado {configuration.tokenizer_name}."
            )
        return result.count

    return make_chunks_from_text(
        text,
        normalized_language,
        configuration.minimum_tokens,
        configuration.target_tokens,
        configuration.maximum_tokens,
        token_counter=real_count,
        token_splitter=split_by_token_limit,
        tokenizer_name=configuration.tokenizer_name,
    )


def split_work(
    *,
    work,
    chunker_version: str = CHUNKER_VERSION,
    tokenizer_name: str = DEFAULT_TOKENIZER_NAME,
    minimum_tokens: int = DEFAULT_MIN_TOKENS,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
    maximum_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    _before_persist: Callable[[], None] | None = None,
) -> SplitResult:
    from author_studio.models import CanonicalText, Work, WorkChunk, WorkSplit, WorkSplitRun

    configuration = SplitConfiguration(
        chunker_version=chunker_version,
        tokenizer_name=tokenizer_name,
        minimum_tokens=minimum_tokens,
        target_tokens=target_tokens,
        maximum_tokens=maximum_tokens,
        overlap_tokens=overlap_tokens,
    )
    canonical = CanonicalText.objects.get(work=work)
    previous_count = WorkChunk.objects.filter(work=work).count()
    run = WorkSplitRun.objects.create(
        work=work,
        canonical_text=canonical,
        source_sha256=canonical.sha256,
        chunker_version=configuration.chunker_version,
        tokenizer_name=configuration.tokenizer_name,
        minimum_tokens=configuration.minimum_tokens,
        target_tokens=configuration.target_tokens,
        maximum_tokens=configuration.maximum_tokens,
        overlap_tokens=configuration.overlap_tokens,
        status=SplitRunStatus.RUNNING.value,
        chunks_previous=previous_count,
    )

    new_storage_names: set[str] = set()
    database_committed = False
    try:
        if configuration.overlap_tokens != 0:
            raise ValueError("A versão atual do Author Studio exige overlap_tokens=0.")
        if not 0 < minimum_tokens <= target_tokens <= maximum_tokens:
            raise ValueError("Parâmetros de chunking inválidos.")
        if _state_is_current(work=work, canonical=canonical, configuration=configuration):
            run.status = SplitRunStatus.COMPLETED.value
            run.outcome = SplitOutcome.ALREADY_CURRENT.value
            run.chunks_preserved = previous_count
            run.completed_at = timezone.now()
            run.save(
                update_fields=["status", "outcome", "chunks_preserved", "completed_at"]
            )
            return SplitResult(
                work.code,
                previous_count,
                SplitStatus.COMPLETED.value,
                SplitOutcome.ALREADY_CURRENT.value,
                chunks_preserved=previous_count,
            )

        text, physical_sha256 = _read_canonical(canonical)
        if physical_sha256 != canonical.sha256:
            raise ValueError("O hash do arquivo canônico não confere com o registro persistido.")
        chunks = _build_chunks(text, work.original_language, configuration)
        _validate_candidates(chunks, configuration)
        if _before_persist:
            _before_persist()

        created = updated = preserved = removed = 0
        obsolete_storage_names: set[str] = set()
        storage = WorkChunk._meta.get_field("text_file").storage

        with transaction.atomic():
            locked_work = Work.objects.select_for_update().get(pk=work.pk)
            locked_canonical = CanonicalText.objects.select_for_update().get(pk=canonical.pk)
            if locked_canonical.sha256 != canonical.sha256:
                raise CanonicalChangedDuringSplit(
                    "O texto canônico mudou durante o processamento; o conjunto anterior foi preservado."
                )
            _, locked_physical_sha256 = _read_canonical(locked_canonical)
            if locked_physical_sha256 != canonical.sha256:
                raise CanonicalChangedDuringSplit(
                    "O arquivo canônico mudou durante o processamento; o conjunto anterior foi preservado."
                )

            state = WorkSplit.objects.select_for_update().filter(work=locked_work).first()
            existing = list(
                WorkChunk.objects.select_for_update().filter(work=locked_work).order_by("sequence")
            )
            existing_by_sequence = {item.sequence: item for item in existing}

            for sequence, chunk in enumerate(chunks, start=1):
                payload = chunk.text.strip().encode("utf-8") + b"\n"
                sha256 = _sha256(payload)
                code = f"{locked_work.code}-CHK{sequence:04d}"
                record = existing_by_sequence.pop(sequence, None)
                common_values = {
                    "work": locked_work,
                    "canonical_text": locked_canonical,
                    "code": code,
                    "sequence": sequence,
                    "unit_type": chunk.unit_type,
                    "unit_title": chunk.unit_title,
                    "sha256": sha256,
                    "character_count": len(payload.decode("utf-8")),
                    "word_count": len(re.findall(r"\b\w+\b", payload.decode("utf-8"), flags=re.UNICODE)),
                    "estimated_tokens": chunk.est_tokens,
                    "token_count": chunk.token_count,
                    "tokenizer_name": configuration.tokenizer_name,
                    "chunker_version": configuration.chunker_version,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                }

                if record and record.code == code and record.sha256 == sha256:
                    for field, value in common_values.items():
                        setattr(record, field, value)
                    record.save(
                        update_fields=[
                            "canonical_text", "unit_type", "unit_title", "character_count",
                            "word_count", "estimated_tokens", "token_count", "tokenizer_name",
                            "chunker_version", "start_line", "end_line",
                        ]
                    )
                    preserved += 1
                    continue

                if record:
                    old_name = record.text_file.name
                    for field, value in common_values.items():
                        setattr(record, field, value)
                    record.text_file.save("chunk.txt", ContentFile(payload), save=False)
                    new_storage_names.add(record.text_file.name)
                    record.save()
                    if old_name != record.text_file.name:
                        obsolete_storage_names.add(old_name)
                    updated += 1
                else:
                    record = WorkChunk(**common_values)
                    record.text_file.save("chunk.txt", ContentFile(payload), save=False)
                    new_storage_names.add(record.text_file.name)
                    record.save()
                    created += 1

            for record in existing_by_sequence.values():
                obsolete_storage_names.add(record.text_file.name)
                record.delete()
                removed += 1

            state_values = {
                "canonical_text": locked_canonical,
                "status": SplitStatus.COMPLETED.value,
                "source_sha256": locked_canonical.sha256,
                "chunker_version": configuration.chunker_version,
                "tokenizer_name": configuration.tokenizer_name,
                "minimum_tokens": configuration.minimum_tokens,
                "target_tokens": configuration.target_tokens,
                "maximum_tokens": configuration.maximum_tokens,
                "overlap_tokens": configuration.overlap_tokens,
                "chunk_count": len(chunks),
                "error": "",
                "started_at": run.started_at,
                "completed_at": timezone.now(),
            }
            if state:
                for field, value in state_values.items():
                    setattr(state, field, value)
                state.save()
            else:
                WorkSplit.objects.create(work=locked_work, **state_values)

            outcome = SplitOutcome.CREATED.value if previous_count == 0 else SplitOutcome.REPROCESSED.value
            run.status = SplitRunStatus.COMPLETED.value
            run.outcome = outcome
            run.chunks_created = created
            run.chunks_updated = updated
            run.chunks_preserved = preserved
            run.chunks_removed = removed
            run.completed_at = timezone.now()
            run.error = ""
            run.save()

            active_names = set(
                WorkChunk.objects.filter(work=locked_work).values_list("text_file", flat=True)
            )
            cleanup_names = obsolete_storage_names - active_names
            transaction.on_commit(
                lambda: (
                    _delete_storage_files(storage, cleanup_names),
                    _remove_unreferenced_work_files(storage, locked_work, active_names),
                ),
                robust=True,
            )

        database_committed = True

        if not _state_is_current(work=work, canonical=locked_canonical, configuration=configuration):
            raise RuntimeError("A validação final do conjunto persistido falhou.")

    except Exception as exc:
        if new_storage_names and not database_committed:
            storage = WorkChunk._meta.get_field("text_file").storage
            _delete_storage_files(storage, new_storage_names)
        _finish_failed_run(run, exc)
        logger.exception("author_studio_split_failed work_id=%s code=%s", work.pk, work.code)
        raise

    logger.info(
        "author_studio_split_completed work_id=%s code=%s chunks=%s outcome=%s",
        work.pk,
        work.code,
        len(chunks),
        outcome,
    )
    return SplitResult(
        work.code,
        len(chunks),
        SplitStatus.COMPLETED.value,
        outcome,
        chunks_created=created,
        chunks_updated=updated,
        chunks_preserved=preserved,
        chunks_removed=removed,
    )


def split_author_works(*, author) -> tuple[list[SplitResult], list[tuple[str, str]]]:
    results: list[SplitResult] = []
    errors: list[tuple[str, str]] = []
    works = author.works.select_related("canonical_text").order_by("id")
    for work in works:
        try:
            results.append(split_work(work=work))
        except Exception as exc:
            errors.append((work.code, str(exc)))
    return results, errors
