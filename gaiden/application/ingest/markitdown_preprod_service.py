from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Protocol

from gaiden.infrastructure import storage
from gaiden.infrastructure import paths
from gaiden.infrastructure.converters.conversion_report import build_markitdown_report
from gaiden.infrastructure.converters.markitdown_adapter import MarkItDownAdapter
from gaiden.tools.heading_inspector import write_inspection_reports


STATE_RAW_UPLOADED = "RAW_UPLOADED"
STATE_INGESTED = "INGESTED"
STATE_MARKITDOWN_EXTRACTED = "MARKITDOWN_EXTRACTED"
STATE_MARKITDOWN_INSPECTED = "MARKITDOWN_INSPECTED"
STATE_MD_READY = "MD_READY"
STATE_NORMALIZED = "NORMALIZED"
STATE_FIXED_TEXT = "FIXED_TEXT"
STATE_CHUNKED = "CHUNKED"


class MarkdownConverter(Protocol):
    def convert_to_markdown(self, source_path: Path) -> str:
        ...


def clean_markitdown_markdown(markdown_text: str) -> str:
    text = markdown_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip() + "\n"


def _ensure_inside_data(path: Path) -> None:
    root = paths.get_data_root(must_exist=True).resolve()
    candidate = path.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Refusing to write outside GAIDEN_DATA_ROOT: {candidate}") from exc
    if "web/data" in candidate.as_posix():
        raise ValueError(f"Refusing to use deprecated web/data path: {candidate}")


def _fail_if_exists(output_paths: list[Path]) -> None:
    existing = [path for path in output_paths if path.exists()]
    if existing:
        joined = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"MarkItDown outputs already exist. Use force=True to overwrite: {joined}")


def run_markitdown_preprod(
    book_code: str,
    lang: str,
    source_path: str | Path,
    promote: bool = True,
    force: bool = False,
    converter: MarkdownConverter | None = None,
) -> dict[str, Any]:
    paths.validate_data_root()
    source = Path(source_path).expanduser()
    if not source.is_absolute():
        source = (storage.repo_root() / source).resolve()
    if "web/data" in source.as_posix():
        raise ValueError(f"Refusing to read deprecated web/data source: {source}")
    if not source.exists():
        raise FileNotFoundError(f"Source file not found: {source}")

    markitdown_dir = paths.get_book_markitdown_dir(book_code, lang)
    md_dir = paths.get_book_md_dir(book_code, lang)
    raw_markdown_path = markitdown_dir / "source_markitdown_raw.md"
    clean_markdown_path = markitdown_dir / "source_markitdown.md"
    markitdown_report_path = markitdown_dir / "markitdown_report.json"
    headings_report_path = markitdown_dir / "headings_report.json"
    chapters_candidates_path = markitdown_dir / "chapters_candidates.json"
    promoted_markdown_path = paths.get_book_source_md_path(book_code, lang) if promote else None

    for output_dir in (markitdown_dir, md_dir):
        _ensure_inside_data(output_dir)
    output_paths = [
        raw_markdown_path,
        clean_markdown_path,
        markitdown_report_path,
        headings_report_path,
        chapters_candidates_path,
    ]
    if promoted_markdown_path:
        output_paths.append(promoted_markdown_path)
    if not force:
        _fail_if_exists(output_paths)

    markitdown_dir.mkdir(parents=True, exist_ok=True)
    md_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    errors: list[str] = []
    status = "SUCCESS"
    converter = converter or MarkItDownAdapter()

    try:
        raw_markdown = converter.convert_to_markdown(source)
        clean_markdown = clean_markitdown_markdown(raw_markdown)
        raw_markdown_path.write_text(raw_markdown if raw_markdown.endswith("\n") else raw_markdown + "\n", encoding="utf-8")
        clean_markdown_path.write_text(clean_markdown, encoding="utf-8")
        headings_report, chapters_report = write_inspection_reports(
            clean_markdown,
            headings_report_path,
            chapters_candidates_path,
        )
        warnings.extend(headings_report.get("warnings", []))
        if promote and promoted_markdown_path:
            if promoted_markdown_path.exists() and not force:
                raise FileExistsError(f"Promoted Markdown already exists: {promoted_markdown_path}")
            shutil.copyfile(clean_markdown_path, promoted_markdown_path)
        if warnings:
            status = "WARN"
    except Exception as exc:
        status = "FAIL"
        errors.append(str(exc))
        headings_report = {"total_headings": 0, "warnings": []}
        chapters_report = {"chapter_candidates": [], "count": 0}
        report = build_markitdown_report(
            book_code=book_code,
            lang=lang,
            source_path=source,
            raw_markdown_path=raw_markdown_path,
            clean_markdown_path=clean_markdown_path,
            promoted_markdown_path=promoted_markdown_path,
            markdown_text="",
            headings_count=0,
            status=status,
            warnings=warnings,
            errors=errors,
        )
        markitdown_report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {
            **report,
            "markitdown_report_path": str(markitdown_report_path),
            "headings_report_path": str(headings_report_path),
            "chapters_candidates_path": str(chapters_candidates_path),
            "state": "FAIL",
        }

    report = build_markitdown_report(
        book_code=book_code,
        lang=lang,
        source_path=source,
        raw_markdown_path=raw_markdown_path,
        clean_markdown_path=clean_markdown_path,
        promoted_markdown_path=promoted_markdown_path,
        markdown_text=clean_markdown,
        headings_count=int(headings_report.get("total_headings", 0)),
        status=status,
        warnings=warnings,
        errors=errors,
    )
    markitdown_report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        **report,
        "markitdown_report_path": str(markitdown_report_path),
        "headings_report_path": str(headings_report_path),
        "chapters_candidates_path": str(chapters_candidates_path),
        "chapters_candidates_count": chapters_report.get("count", 0),
        "state": STATE_MD_READY if promote else STATE_MARKITDOWN_INSPECTED,
    }
