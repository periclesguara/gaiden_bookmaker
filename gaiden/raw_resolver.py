from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


LANG_DIR_MAP = {
    "ptbr": "ptbr",
    "pt-br": "ptbr",
    "pt_br": "ptbr",
    "en": "en",
    "es": "es",
    "de": "de",
    "fr": "fr",
    "it": "it",
}


def normalize_lang_fs(lang: str) -> str:
    raw = (lang or "en").strip()
    key = raw.lower()
    if key in LANG_DIR_MAP:
        return LANG_DIR_MAP[key]
    return key.replace("-", "").replace("_", "")


def canonical_raw_dir(book_code: str, lang: str, data_dir: Path) -> Path:
    lang_code = normalize_lang_fs(lang)
    return data_dir / "raw" / book_code / lang_code


def _lang_variants(lang: str, canonical: str) -> list[str]:
    variants: list[str] = []

    def add(value: str) -> None:
        if value and value not in variants:
            variants.append(value)

    add(canonical)
    add(canonical.lower())
    if canonical == "ptbr":
        for item in ["pt-br", "pt_br", "PT-BR", "PT_BR", "PTBR", "ptbr"]:
            add(item)
    else:
        add(canonical.upper())

    raw = (lang or "").strip()
    if raw:
        add(raw)
        add(raw.lower())
        add(raw.upper())
        add(raw.replace("-", "_"))
        add(raw.replace("_", "-"))
        add(raw.replace("-", ""))
        add(raw.replace("_", ""))

    return variants


def _candidate_raw_dirs(book_code: str, lang: str, data_dir: Path) -> Iterable[Path]:
    canonical = normalize_lang_fs(lang)
    base = data_dir / "raw" / book_code
    for variant in _lang_variants(lang, canonical):
        yield base / variant


def _select_source(dir_path: Path) -> Path | None:
    txt_path = dir_path / "source.txt"
    md_path = dir_path / "source.md"
    txt_exists = txt_path.exists()
    md_exists = md_path.exists()
    if txt_exists and md_exists:
        raise ValueError(f"INVALID_STATE: multiple RAW sources ({txt_path}, {md_path})")
    if txt_exists:
        return txt_path
    if md_exists:
        return md_path
    return None


@dataclass(frozen=True)
class RawResolution:
    raw_path: Path
    selected_dir: Path
    canonical_dir: Path
    alias_created: Path | None


def resolve_raw_source(
    book_code: str,
    lang: str,
    data_dir: Path,
    *,
    create_alias: bool = True,
    logger: Callable[[str], None] | None = print,
) -> RawResolution:
    canonical_dir = canonical_raw_dir(book_code, lang, data_dir)

    for dir_path in _candidate_raw_dirs(book_code, lang, data_dir):
        source = _select_source(dir_path)
        if not source:
            continue

        if logger is not None:
            logger(f"RAW_DIR_SELECTED: {dir_path}")

        alias_created = None
        if dir_path != canonical_dir and create_alias:
            canonical_dir.mkdir(parents=True, exist_ok=True)
            alias_name = "source.md" if source.suffix.lower() == ".md" else "source.txt"
            alias_path = canonical_dir / alias_name
            if not alias_path.exists():
                shutil.copy2(source, alias_path)
                alias_created = alias_path
                if logger is not None:
                    logger(f"RAW_ALIAS_CREATED: {alias_path}")
            raw_path = alias_path if alias_path.exists() else source
        else:
            raw_path = source

        return RawResolution(
            raw_path=raw_path,
            selected_dir=dir_path,
            canonical_dir=canonical_dir,
            alias_created=alias_created,
        )

    raise FileNotFoundError(f"RAW_MISSING: {canonical_dir}/source.(txt|md)")
