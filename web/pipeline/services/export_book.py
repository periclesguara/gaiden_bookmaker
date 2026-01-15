from __future__ import annotations

import subprocess

from . import paths


def run_export_epub(edition) -> dict:
    in_path = paths.build_md_path(edition)
    if not in_path.exists():
        raise FileNotFoundError(f"Build file not found: {in_path}")

    out_path = paths.epub_path(edition)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "pandoc",
        str(in_path),
        "-o",
        str(out_path),
        "--toc",
        "--toc-depth=2",
        "--ebook-format=epub3",
    ]
    subprocess.run(cmd, check=True)

    return {"path": str(out_path), "cmd": " ".join(cmd)}


def run_export_pdf(edition) -> dict:
    in_path = paths.build_md_path(edition)
    if not in_path.exists():
        raise FileNotFoundError(f"Build file not found: {in_path}")

    out_path = paths.pdf_path(edition)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "pandoc",
        str(in_path),
        "-o",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)

    return {"path": str(out_path), "cmd": " ".join(cmd)}


def run_epubcheck(edition) -> dict:
    epub = paths.epub_path(edition)
    if not epub.exists():
        raise FileNotFoundError(f"EPUB not found: {epub}")

    cmd = [
        "epubcheck",
        str(epub),
    ]
    subprocess.run(cmd, check=True)

    return {"path": str(epub), "cmd": " ".join(cmd)}
