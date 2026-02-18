from __future__ import annotations

import json
import re
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
CHUNK_FILE_RE = re.compile(r"^ch_(\d+)_chunk_(\d+)\.txt$", re.IGNORECASE)
TEXTUAL_CHAPTER_RE = re.compile(
    r"^\s*(chapter\s+\d+|[ivxlcdm]{1,8}\s*$|[ivxlcdm]{1,8}\.\s+\S+|[0-9]+\.\s+\S+)",
    re.IGNORECASE,
)


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


def _score_loose_txt_candidate(path: Path, *, book_code: str, language: str) -> tuple[int, int, float, str]:
    name = path.name.lower()
    book_code_norm = book_code.lower()
    lang_norm = utils.normalize_lang(language).lower()
    score = 0
    if book_code_norm in name:
        score += 40
    if lang_norm in name:
        score += 20
    if any(token in name for token in ("merge", "source", "raw", "final", "book")):
        score += 8
    if "chunk" in name:
        score -= 15
    if "report" in name or "manifest" in name:
        score -= 20
    try:
        size = path.stat().st_size
        mtime = path.stat().st_mtime
    except OSError:
        size = 0
        mtime = 0.0
    return score, size, mtime, path.name


def _best_loose_txt_in_build_dir(build_dir: Path, *, book_code: str, language: str) -> Path | None:
    if not build_dir.exists():
        return None
    candidates: list[Path] = []
    for path in build_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() != ".txt":
            continue
        candidates.append(path)
    if not candidates:
        return None
    ranked = sorted(
        candidates,
        key=lambda p: _score_loose_txt_candidate(
            p,
            book_code=book_code,
            language=language,
        ),
        reverse=True,
    )
    return ranked[0]


def _best_loose_txt_in_raw_dir(book_code: str, language: str) -> Path | None:
    data_root = paths.data_dir()
    raw_dir = data_root / "raw" / book_code
    if not raw_dir.exists():
        return None
    candidates: list[Path] = []
    for path in raw_dir.rglob("*.txt"):
        if not path.is_file():
            continue
        candidates.append(path)
    if not candidates:
        return None
    ranked = sorted(
        candidates,
        key=lambda p: _score_loose_txt_candidate(
            p,
            book_code=book_code,
            language=language,
        ),
        reverse=True,
    )
    return ranked[0]


def _ordered_chunk_paths(chunks_dir: Path) -> list[Path]:
    manifest_path = chunks_dir / "chunks_manifest.json"
    ordered: list[Path] = []
    seen: set[str] = set()

    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = None
        if isinstance(manifest, dict):
            chapters = manifest.get("chapters")
            if isinstance(chapters, list):
                for chapter in chapters:
                    if not isinstance(chapter, dict):
                        continue
                    chunks = chapter.get("chunks")
                    if not isinstance(chunks, list):
                        continue
                    for item in chunks:
                        if not isinstance(item, dict):
                            continue
                        file_name = str(item.get("file_path") or "").strip()
                        if not file_name:
                            continue
                        path = chunks_dir / file_name
                        if not path.exists():
                            continue
                        key = str(path)
                        if key in seen:
                            continue
                        seen.add(key)
                        ordered.append(path)

    if ordered:
        return ordered

    matched: list[tuple[int, int, Path]] = []
    for path in chunks_dir.glob("ch_*_chunk_*.txt"):
        m = CHUNK_FILE_RE.match(path.name)
        if not m:
            continue
        matched.append((int(m.group(1)), int(m.group(2)), path))
    matched.sort(key=lambda t: (t[0], t[1]))
    return [path for _, _, path in matched]


def _materialize_chunk_source_txt(book_code: str, language: str, build_dir: Path) -> Path | None:
    data_root = paths.data_dir()
    normalized_lang = utils.normalize_lang(language)
    chunk_langs = [normalized_lang]
    if normalized_lang != "en":
        chunk_langs.append("en")

    chunk_files: list[Path] = []
    for chunk_lang in chunk_langs:
        chunks_dir = data_root / "chunks" / book_code / chunk_lang
        if not chunks_dir.exists():
            continue
        ordered = _ordered_chunk_paths(chunks_dir)
        if ordered:
            chunk_files = ordered
            break
    if not chunk_files:
        return None

    target = build_dir / f"source_from_chunks_{normalized_lang}.txt"
    chunks_text: list[str] = []
    for chunk_path in chunk_files:
        text = chunk_path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        chunks_text.append(text)
    if not chunks_text:
        return None

    has_textual_chapters = any(
        TEXTUAL_CHAPTER_RE.search(line.strip())
        for text in chunks_text
        for line in text.splitlines()
    )

    image_raw_dir = data_root / "images" / book_code / normalized_lang / "raw"
    image_count = 0
    if image_raw_dir.exists():
        image_count = sum(1 for p in image_raw_dir.iterdir() if p.is_file())

    use_synthetic = (
        not has_textual_chapters
        and image_count >= 2
        and image_count <= len(chunks_text)
    )

    parts: list[str] = []
    if use_synthetic:
        total_chunks = len(chunks_text)
        chapter_count = image_count
        base = total_chunks // chapter_count
        rem = total_chunks % chapter_count
        starts: list[int] = []
        cursor = 0
        for i in range(chapter_count):
            starts.append(cursor)
            size = base + (1 if i < rem else 0)
            cursor += size
        start_to_idx = {start: idx + 1 for idx, start in enumerate(starts)}

        for idx, text in enumerate(chunks_text):
            chapter_no = start_to_idx.get(idx)
            if chapter_no is not None:
                parts.append(f"CHAPTER {chapter_no:02d}")
                parts.append("")
            parts.append(text)
    else:
        parts.extend(chunks_text)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n\n".join(parts).strip() + "\n", encoding="utf-8")
    return target


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
    book_code = edition_meta.book_code(edition)
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

    loose_raw = _best_loose_txt_in_raw_dir(book_code, normalized_lang)
    if loose_raw:
        return SelectedTextSource(
            language=normalized_lang,
            path=loose_raw,
            name=loose_raw.name,
            label=f"{loose_raw.name} ({normalized_lang}, raw fallback)",
        )

    loose_build = _best_loose_txt_in_build_dir(
        build_dir,
        book_code=book_code,
        language=normalized_lang,
    )
    if loose_build and not loose_build.name.startswith("source_from_chunks_"):
        return SelectedTextSource(
            language=normalized_lang,
            path=loose_build,
            name=loose_build.name,
            label=f"{loose_build.name} ({normalized_lang}, build fallback)",
        )

    chunk_source = _materialize_chunk_source_txt(book_code, normalized_lang, build_dir)
    if chunk_source:
        return SelectedTextSource(
            language=normalized_lang,
            path=chunk_source,
            name=chunk_source.name,
            label=f"{chunk_source.name} ({normalized_lang}, chunks fallback)",
        )

    if loose_build:
        return SelectedTextSource(
            language=normalized_lang,
            path=loose_build,
            name=loose_build.name,
            label=f"{loose_build.name} ({normalized_lang}, build fallback)",
        )

    raise FileNotFoundError(
        f"Nenhum TXT fonte encontrado em {build_dir} (lang={normalized_lang})."
    )
