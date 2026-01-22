from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings

from editorial import kdp_mode
from . import edition_meta, paths


def _resolve_cover_path(edition) -> Path | None:
    cover = (getattr(edition, "cover_filepath", "") or "").strip()
    if not cover:
        return None
    path = Path(cover)
    if not path.is_absolute():
        path = Path(settings.BASE_DIR).parent / path
    return path if path.exists() else None


def _ensure_epub_cover_meta(epub_path: Path) -> None:
    opf_path = "EPUB/content.opf"
    with zipfile.ZipFile(epub_path, "r") as zin:
        if opf_path not in zin.namelist():
            return
        opf_text = zin.read(opf_path).decode("utf-8", errors="replace")

    if 'name="cover"' in opf_text:
        return

    cover_id = None
    for match in re.finditer(r"<item[^>]+>", opf_text):
        tag = match.group(0)
        if "cover-image" not in tag:
            continue
        id_match = re.search(r'id="([^"]+)"', tag)
        if id_match:
            cover_id = id_match.group(1)
            break

    if not cover_id or "</metadata>" not in opf_text:
        return

    opf_text = opf_text.replace(
        "</metadata>",
        f'    <meta name="cover" content="{cover_id}" />\n  </metadata>',
        1,
    )

    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        tmp_path = Path(tmp_file.name)

    try:
        with zipfile.ZipFile(epub_path, "r") as zin, zipfile.ZipFile(
            tmp_path, "w"
        ) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == opf_path:
                    data = opf_text.encode("utf-8")
                zout.writestr(info, data)
        shutil.move(tmp_path, epub_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _resolve_build_dir(edition, language_override: str | None = None) -> Path:
    if language_override:
        return paths.edition_build_dir_for_language(
            edition_meta.book_code(edition), language_override
        )
    return paths.edition_build_dir(edition)


def run_export_epub(edition, language_override: str | None = None) -> dict:
    target = edition
    if language_override:
        target = edition.__class__.objects.get(
            work__code=edition_meta.book_code(edition),
            language__code=language_override,
        )

    out_path = kdp_mode.build_epub_for_edition(target, epub_filename="BOOK.epub")
    cover_path = _resolve_cover_path(target)
    if cover_path:
        _ensure_epub_cover_meta(Path(out_path))

    return {"path": str(out_path), "cmd": "kdp_mode.build_epub_for_edition"}


def run_export_pdf(edition, language_override: str | None = None) -> dict:
    build_dir = _resolve_build_dir(edition, language_override)
    in_path = build_dir / "BOOK.BUILD.MD"
    if not in_path.exists():
        raise FileNotFoundError(f"Build file not found: {in_path}")

    out_path = build_dir / "BOOK.PRINT.PDF"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "pandoc",
        str(in_path),
        "-o",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)

    return {"path": str(out_path), "cmd": " ".join(cmd)}


def run_epubcheck(edition, language_override: str | None = None) -> dict:
    build_dir = _resolve_build_dir(edition, language_override)
    epub = build_dir / "BOOK.epub"
    if not epub.exists():
        raise FileNotFoundError(f"EPUB not found: {epub}")

    cmd = [
        "epubcheck",
        str(epub),
    ]
    subprocess.run(cmd, check=True)

    return {"path": str(epub), "cmd": " ".join(cmd)}
