from __future__ import annotations

from datetime import datetime
from pathlib import Path

from django.db import transaction

from editorial.models import PipelineArtifact

ROOT = Path(__file__).resolve().parents[3]


def _stat(path: Path) -> tuple[int, str]:
    st = path.stat()
    return st.st_size, datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")


def _relpath(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT).as_posix())
    except ValueError:
        return str(path.as_posix())


def _upsert(work_code: str, lang: str, stage: str, path: Path, is_candidate: bool = True) -> None:
    rel = _relpath(path)
    size, mtime = _stat(path)
    PipelineArtifact.objects.update_or_create(
        work_code=work_code,
        language_code=lang,
        stage=stage,
        relpath=rel,
        defaults={
            "filename": path.name,
            "size_bytes": size,
            "mtime_iso": mtime,
            "exists": True,
            "is_candidate": is_candidate,
        },
    )


def _scan_builds(work_code: str, lang: str) -> None:
    bdir = ROOT / "data" / "builds" / work_code / lang
    if not bdir.exists():
        return

    patterns = [
        (
            "translate",
            [
                f"merge_translate_{lang}.txt",
                "merge_translate.txt",
                f"translate_{lang}.txt",
                "translate.txt",
            ],
        ),
        (
            "refine",
            [
                f"merge_refine_{lang}.txt",
                "merge_refine.txt",
                f"refine_{lang}.txt",
                "refine.txt",
            ],
        ),
        (
            "polish",
            [
                f"merge_polish_{lang}.txt",
                "merge_polish.txt",
                f"polish_{lang}.txt",
                "polish.txt",
            ],
        ),
    ]
    for stage, names in patterns:
        for name in names:
            path = bdir / name
            if path.exists():
                _upsert(work_code, lang, stage, path, is_candidate=name.startswith("merge_"))

    miolo = bdir / "MIOL_TERM.v1.md"
    if miolo.exists():
        _upsert(work_code, lang, "miolo", miolo, is_candidate=True)

    finals = [
        ("BOOK.MD_FINAL", "build"),
        ("BOOK.BUILD.MD", "build"),
        ("BOOK.EPUB3", "epub"),
        ("ebook.epub", "epub"),
        ("BOOK.PRINT.PDF", "pdf"),
    ]
    for name, stage in finals:
        path = bdir / name
        if path.exists():
            _upsert(work_code, lang, stage, path, is_candidate=True)


def _scan_frontmatter(work_code: str, lang: str) -> None:
    fdir = ROOT / "data" / "frontmatter" / work_code / lang
    if not fdir.exists():
        return
    for name in [
        "title_page.md",
        "frontispiece.md",
        "copyright.md",
        "source_record.md",
        "about_edition.md",
        "about_contributor.md",
    ]:
        path = fdir / name
        if path.exists():
            _upsert(work_code, lang, "frontmatter", path, is_candidate=True)


def _scan_translated(work_code: str, lang: str) -> None:
    tdir = ROOT / "data" / "translated" / work_code / lang
    if not tdir.exists():
        return
    path = tdir / "miolo.md"
    if path.exists():
        _upsert(work_code, lang, "miolo", path, is_candidate=True)


def _scan_cover(work_code: str, lang: str) -> None:
    cdir = ROOT / "data" / "covers" / work_code / lang
    if not cdir.exists():
        return
    for name in ["cover.jpg", "cover.png"]:
        path = cdir / name
        if path.exists():
            _upsert(work_code, lang, "cover", path, is_candidate=True)


@transaction.atomic
def reindex_artifacts_for_work(work_code: str, langs: tuple[str, ...] = ("en", "de", "es", "ptbr")) -> None:
    PipelineArtifact.objects.filter(work_code=work_code).delete()

    for lang in langs:
        _scan_builds(work_code, lang)
        _scan_frontmatter(work_code, lang)
        _scan_translated(work_code, lang)
        _scan_cover(work_code, lang)
