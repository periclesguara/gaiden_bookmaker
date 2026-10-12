"""Standalone command line interface for Gaiden's final print stage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .compiler import compile_lualatex
from .latex import BookContent, Chapter, LatexInteriorRenderer
from .preflight import inspect_pdf_page_count, preflight_pdfs
from .specs import PrintSpec


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def load_book_content(path: str | Path) -> BookContent:
    """Load approved editorial content without depending on Writer or Django."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid book JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("book JSON must contain an object")

    raw_chapters = payload.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        raise ValueError("chapters must be a non-empty list")
    chapters: list[Chapter] = []
    for index, raw_chapter in enumerate(raw_chapters, start=1):
        if not isinstance(raw_chapter, dict):
            raise ValueError(f"chapters[{index}] must be an object")
        chapters.append(
            Chapter(
                title=_required_text(raw_chapter.get("title"), field=f"chapters[{index}].title"),
                body=_required_text(raw_chapter.get("body"), field=f"chapters[{index}].body"),
            )
        )

    return BookContent.from_chapters(
        title=_required_text(payload.get("title"), field="title"),
        author=_required_text(payload.get("author"), field="author"),
        chapters=chapters,
        subtitle=str(payload.get("subtitle", "")),
        language=str(payload.get("language", "en-US")),
        publisher=str(payload.get("publisher", "RinoBooks")),
        copyright_text=str(payload.get("copyright_text", "")),
    )


def _build_interior(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    content = load_book_content(args.input)
    source = output_dir / "INTERIOR.tex"
    source.write_text(
        LatexInteriorRenderer().render(content, PrintSpec()),
        encoding="utf-8",
    )
    result = compile_lualatex(source, output_dir=output_dir)
    page_count = inspect_pdf_page_count(result.pdf_path)
    print(
        json.dumps(
            {
                "status": "built",
                "interior_tex": str(source),
                "interior_pdf": str(result.pdf_path),
                "page_count": page_count,
            },
            indent=2,
        )
    )
    return 0


def _preflight(args: argparse.Namespace) -> int:
    spec = PrintSpec().finalize(
        page_count=args.page_count,
        spine_width_in=args.spine_width,
    )
    report = preflight_pdfs(
        spec,
        interior_pdf=args.interior,
        cover_pdf=args.cover,
    )
    print(json.dumps(report.as_dict(), indent=2))
    return 0 if report.passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gaiden-print",
        description="Build and preflight final print PDFs from approved editorial content.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build-interior", help="render and compile INTERIOR.pdf")
    build.add_argument("--input", required=True, help="approved book JSON")
    build.add_argument("--output-dir", required=True, help="external runtime output directory")
    build.set_defaults(handler=_build_interior)

    preflight = commands.add_parser("preflight", help="validate final INTERIOR.pdf and COVER.pdf")
    preflight.add_argument("--interior", required=True)
    preflight.add_argument("--cover", required=True)
    preflight.add_argument("--page-count", required=True, type=int)
    preflight.add_argument("--spine-width", required=True)
    preflight.set_defaults(handler=_preflight)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"gaiden-print: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
