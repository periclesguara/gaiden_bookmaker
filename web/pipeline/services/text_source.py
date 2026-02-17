from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gaiden.translate_artifacts import list_canonical_artifacts, resolve_active_or_latest

from . import edition_meta, paths, stage_policy, utils


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


def _translated_language_aliases(language_code: str) -> list[str]:
    aliases = [language_code]
    if utils.normalize_lang(language_code) == "en" and "en_modern" not in aliases:
        aliases.append("en_modern")
    return aliases


def _translated_txt_candidates(book_code: str, language_code: str) -> list[tuple[str, Path, str]]:
    data_root = paths.data_dir()
    found: list[tuple[str, Path, str]] = []
    seen: set[str] = set()

    for alias in _translated_language_aliases(language_code):
        translated_dir = data_root / "translated" / book_code / alias

        active = resolve_active_or_latest(translated_dir, book_code, alias)
        if active and active.exists():
            key = str(active)
            if key not in seen:
                seen.add(key)
                found.append((alias, active, f"{active.name} (translated/{alias}, active)"))

        for path in list_canonical_artifacts(translated_dir, book_code, alias):
            if not path.exists():
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            found.append((alias, path, f"{path.name} (translated/{alias})"))

        legacy_candidates = [
            translated_dir / f"{book_code}_merge_refine_clean.txt",
            translated_dir / "merge_refine_clean.txt",
            translated_dir / f"merge_translate_{alias}.txt",
            translated_dir / "merge_translate.txt",
            translated_dir / f"{book_code}_{alias}_merged_v1.txt",
        ]
        for path in legacy_candidates:
            if not path.exists():
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            found.append((alias, path, f"{path.name} (translated/{alias}, legacy)"))

    return found


def _discover_merge_candidates(build_dir: Path, language: str) -> list[TextSourceCandidate]:
    candidates: list[TextSourceCandidate] = []
    book_code = build_dir.parent.name
    for alias, path, label in _translated_txt_candidates(book_code, language):
        candidates.append(
            TextSourceCandidate(
                value=_pack_value(alias, str(path)),
                label=label,
            )
        )
    return candidates


def _latest_job_candidates(edition, stages: tuple[str, ...]) -> list[TextSourceCandidate]:
    return []


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
    language_code = edition_meta.language_code(edition)

    def resolve_one(raw_value: str) -> SelectedTextSource | None:
        lang, value = _unpack_value(raw_value, language_code)
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
        book_code = edition_meta.book_code(edition)
        candidates = _translated_txt_candidates(book_code, language_code)
        if candidates:
            alias, path, _label = candidates[0]
            return [
                SelectedTextSource(
                    language=alias,
                    path=path,
                    name=path.name,
                    label=f"{path.name} ({alias})",
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
    language_code = edition_meta.language_code(edition)
    candidates = _discover_merge_candidates(build_dir, language_code)
    policy = stage_policy.POLICY
    candidates.extend(_latest_job_candidates(edition, policy.stages_for(edition)))
    candidates = _dedupe_candidates(candidates)

    mode = getattr(edition, "text_source_mode", "auto") or "auto"
    selected_sources = _resolve_selected_sources(edition)

    canonical_name = selected_sources[0].name if selected_sources else None
    canonical_path = selected_sources[0].path if selected_sources else None

    job_stage = None
    job_filepath = None
    job_id = None
    job_created_at = None

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
            if candidate.value not in {_pack_value(language_code, name) for name in paths.MERGE_PRIORITY}
        ],
        selected_values=_build_selected_values(mode, language_code),
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


def resolve_txt_source(edition) -> SelectedTextSource:
    selected_sources = _resolve_selected_sources(edition)
    if selected_sources:
        return selected_sources[0]

    build_dir = paths.edition_build_dir(edition)
    lang_code = edition_meta.language_code(edition)
    normalized_lang = utils.normalize_lang(lang_code)
    policy = stage_policy.POLICY

    def pick(candidates: list[str]) -> Path | None:
        for name in candidates:
            path = build_dir / name
            if path.exists():
                return path
        return None

    def stage_candidates(stage: str) -> list[str]:
        names = [
            f"merge_{stage}_{normalized_lang}.txt",
            f"{stage}_{normalized_lang}.txt",
        ]
        if normalized_lang != lang_code:
            names.extend(
                [
                    f"merge_{stage}_{lang_code}.txt",
                    f"{stage}_{lang_code}.txt",
                ]
            )
        names.extend([f"merge_{stage}.txt", f"{stage}.txt"])
        return names

    manual_stage = getattr(edition, "miolo_source_stage", "") or ""
    if manual_stage:
        manual_path = pick(stage_candidates(manual_stage))
        if not manual_path:
            raise FileNotFoundError(
                f"Stage '{manual_stage}' selecionado, mas TXT nao encontrado em {build_dir}."
            )
        return SelectedTextSource(
            language=normalized_lang,
            path=manual_path,
            name=manual_path.name,
            label=f"{manual_path.name} ({normalized_lang})",
        )

    locked_stage = policy.locked_reference_stage(edition)
    if locked_stage:
        locked_path = pick(stage_candidates(locked_stage))
        if not locked_path:
            raise FileNotFoundError(
                f"Stage '{locked_stage}' esta LOCKED, mas TXT nao encontrado em {build_dir}."
            )
        return SelectedTextSource(
            language=normalized_lang,
            path=locked_path,
            name=locked_path.name,
            label=f"{locked_path.name} ({normalized_lang})",
        )

    for stage in policy.stages_for(edition):
        stage_path = pick(stage_candidates(stage))
        if stage_path:
            return SelectedTextSource(
                language=normalized_lang,
                path=stage_path,
                name=stage_path.name,
                label=f"{stage_path.name} ({normalized_lang})",
            )

    raise FileNotFoundError(
        f"Nenhum TXT fonte encontrado em {build_dir} (lang={normalized_lang})."
    )
