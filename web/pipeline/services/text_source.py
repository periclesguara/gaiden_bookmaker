from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pipeline.models import PipelineJob
from . import paths


@dataclass
class TextSourceInfo:
    canonical_name: str | None
    canonical_path: Path | None
    job_stage: str | None
    job_filepath: str | None
    job_id: int | None
    job_created_at: str | None
    mode: str
    extra_candidates: list["TextSourceCandidate"]
    selected_values: list[str]
    selected_sources: list["SelectedTextSource"]


@dataclass
class TextSourceCandidate:
    value: str
    label: str


@dataclass
class SelectedTextSource:
    language: str
    path: Path
    name: str
    label: str


MODE_SEPARATOR = "||"


def _pack_value(language: str, path_value: str) -> str:
    return f"{language}::{path_value}"


def _unpack_value(raw_value: str, fallback_language: str) -> tuple[str, str]:
    if "::" in raw_value:
        lang, value = raw_value.split("::", 1)
        return lang or fallback_language, value
    return fallback_language, raw_value


def _discover_merge_candidates(build_dir: Path, language: str) -> list[TextSourceCandidate]:
    candidates: list[TextSourceCandidate] = []
    names: set[str] = set()
    for name in paths.MERGE_PRIORITY:
        if (build_dir / name).exists():
            names.add(name)
            candidates.append(
                TextSourceCandidate(
                    value=_pack_value(language, name),
                    label=f"{name} ({language})",
                )
            )

    for path in sorted(build_dir.glob("*.txt")):
        if path.name not in names:
            names.add(path.name)
            candidates.append(
                TextSourceCandidate(
                    value=_pack_value(language, path.name),
                    label=f"{path.name} ({language})",
                )
            )
    return candidates


def _latest_job_candidates(edition, stages: tuple[str, ...]) -> list[TextSourceCandidate]:
    jobs = (
        PipelineJob.objects.filter(
            book_code=edition.book_code,
            stage__in=stages,
            status="SUCCESS",
        )
        .order_by("-created_at")
    )
    seen: set[tuple[str, str]] = set()
    candidates: list[TextSourceCandidate] = []
    for job in jobs:
        if not job.language:
            continue
        key = (job.language, job.stage)
        if key in seen:
            continue
        if not job.filepath:
            continue
        path = Path(job.filepath)
        if not path.exists():
            continue
        seen.add(key)
        timestamp = job.created_at.strftime("%Y-%m-%d %H:%M")
        label = f"{job.stage} ({job.language}) - {path.name} [{timestamp}]"
        candidates.append(
            TextSourceCandidate(
                value=_pack_value(job.language, str(path)),
                label=label,
            )
        )
        clean_path = path.with_name(f"{path.stem}_clean{path.suffix}")
        if clean_path.exists():
            clean_label = f"clean ({job.language}) - {clean_path.name} [{timestamp}]"
            candidates.append(
                TextSourceCandidate(
                    value=_pack_value(job.language, str(clean_path)),
                    label=clean_label,
                )
            )
    return candidates


def _dedupe_candidates(candidates: list[TextSourceCandidate]) -> list[TextSourceCandidate]:
    seen: set[str] = set()
    unique: list[TextSourceCandidate] = []
    for candidate in candidates:
        if candidate.value in seen:
            continue
        seen.add(candidate.value)
        unique.append(candidate)
    return unique


def _resolve_selected_sources(edition) -> list[SelectedTextSource]:
    build_dir = paths.edition_build_dir(edition)
    mode = getattr(edition, "text_source_mode", "auto") or "auto"

    def resolve_one(raw_value: str) -> SelectedTextSource | None:
        lang, value = _unpack_value(raw_value, edition.language)
        candidate_path = Path(value)
        if not candidate_path.is_absolute():
            candidate_path = build_dir / value
        if not candidate_path.exists():
            return None
        return SelectedTextSource(
            language=lang,
            path=candidate_path,
            name=candidate_path.name,
            label=f"{candidate_path.name} ({lang})",
        )

    def resolve_auto() -> list[SelectedTextSource]:
        for name in paths.merge_priority_names(edition):
            p = build_dir / name
            if p.exists():
                return [
                    SelectedTextSource(
                        language=edition.language,
                        path=p,
                        name=p.name,
                        label=f"{p.name} ({edition.language})",
                    )
                ]
        return []

    if mode == "auto":
        return resolve_auto()

    raw_values = [value for value in mode.split(MODE_SEPARATOR) if value]
    sources = [resolved for value in raw_values if (resolved := resolve_one(value))]
    if sources:
        return sources
    return resolve_auto()


def get_effective_text_source(edition) -> TextSourceInfo:
    build_dir = paths.edition_build_dir(edition)
    candidates = _discover_merge_candidates(build_dir, edition.language)
    candidates.extend(_latest_job_candidates(edition, ("translate", "refine", "polish")))
    candidates = _dedupe_candidates(candidates)

    mode = getattr(edition, "text_source_mode", "auto") or "auto"
    selected_sources = _resolve_selected_sources(edition)

    canonical_name = selected_sources[0].name if selected_sources else None
    canonical_path = selected_sources[0].path if selected_sources else None

    job_stage = None
    job_filepath = None
    job_id = None
    job_created_at = None

    if canonical_path:
        job = (
            PipelineJob.objects.filter(
                filepath=str(canonical_path),
            )
            .order_by("-created_at")
            .first()
        )
        if not job and canonical_name:
            if "polish" in canonical_name:
                stage = "polish"
            elif "refine" in canonical_name:
                stage = "refine"
            elif "translate" in canonical_name:
                stage = "translate"
            else:
                stage = "refine"
            job = (
                PipelineJob.objects.filter(
                    book_code=edition.book_code,
                    language=edition.language,
                    stage=stage,
                )
                .order_by("-created_at")
                .first()
            )
        if job:
            job_stage = job.stage
            job_filepath = job.filepath
            job_id = job.id
            job_created_at = job.created_at.isoformat()

    return TextSourceInfo(
        canonical_name=canonical_name,
        canonical_path=canonical_path,
        job_stage=job_stage,
        job_filepath=job_filepath,
        job_id=job_id,
        job_created_at=job_created_at,
        mode=mode,
        extra_candidates=[
            candidate
            for candidate in candidates
            if candidate.value not in {_pack_value(edition.language, name) for name in paths.MERGE_PRIORITY}
        ],
        selected_values=_build_selected_values(mode, edition.language),
        selected_sources=selected_sources,
    )


def _build_selected_values(mode: str, language: str) -> list[str]:
    if mode == "auto":
        return ["auto"]
    values = [value for value in mode.split(MODE_SEPARATOR) if value]
    normalized: list[str] = []
    for value in values:
        normalized.append(value)
        if "::" not in value:
            normalized.append(_pack_value(language, value))
    return normalized


def resolve_selected_text_sources(edition) -> list[SelectedTextSource]:
    return _resolve_selected_sources(edition)
