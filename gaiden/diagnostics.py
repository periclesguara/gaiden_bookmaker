from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

DB_PATH = Path("data/db/gaiden.sqlite3")
CHUNKS_ROOT = Path("data/chunks")
CANONICAL_BOOK = "book_0003"

ALL_CHECKS = [
    "chunks",
    "normalized",
]


@dataclass
class BookDiagnostics:
    book_id: int
    indexed_ok: bool
    file_ok: bool
    file_path: Path | None
    extracted_ok: bool
    normalized_ok: bool
    chunk_ok: bool
    chunk_count: int


def get_book_diagnostics(book_id: int) -> BookDiagnostics:
    indexed_ok = False
    file_ok = False
    file_path: Path | None = None
    extracted_ok = False
    normalized_ok = False
    chunk_ok = False
    chunk_count = 0

    if not DB_PATH.exists():
        return BookDiagnostics(
            book_id=book_id,
            indexed_ok=False,
            file_ok=False,
            file_path=None,
            extracted_ok=False,
            normalized_ok=False,
            chunk_ok=False,
            chunk_count=0,
        )

    conn = sqlite3.connect(DB_PATH.as_posix())
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone()
        indexed_ok = row is not None

        row = conn.execute(
            "SELECT stored_path FROM book_files WHERE book_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (book_id,),
        ).fetchone()
        if row:
            p = Path(row["stored_path"])
            file_path = p
            file_ok = p.exists()

        row = conn.execute(
            "SELECT extracted_text FROM book_text WHERE book_id = ?",
            (book_id,),
        ).fetchone()
        if row and row["extracted_text"]:
            extracted_ok = True

        try:
            row = conn.execute(
                "SELECT normalized_text FROM book_normalized WHERE book_id = ?",
                (book_id,),
            ).fetchone()
            if row and row["normalized_text"]:
                normalized_ok = True
        except sqlite3.OperationalError:
            normalized_ok = False
    finally:
        conn.close()

    chunks_dir = CHUNKS_ROOT / f"book_{book_id:04d}" / "en"
    if chunks_dir.exists():
        files = list(chunks_dir.glob("*.txt"))
        chunk_count = len(files)
        chunk_ok = chunk_count > 0

    return BookDiagnostics(
        book_id=book_id,
        indexed_ok=indexed_ok,
        file_ok=file_ok,
        file_path=file_path,
        extracted_ok=extracted_ok,
        normalized_ok=normalized_ok,
        chunk_ok=chunk_ok,
        chunk_count=chunk_count,
    )


def _parse_book_id(book: str) -> int:
    m = re.match(r"^book_(\d{4})$", book.strip())
    if not m:
        raise ValueError(f"Livro inválido: {book}")
    return int(m.group(1))


def _load_json(path: Path) -> Dict[str, object]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _check_chunks(book: str) -> str:
    book_id = _parse_book_id(book)
    book_code = f"book_{book_id:04d}"
    chunks_dir = CHUNKS_ROOT / book_code / "en"
    manifest_path = chunks_dir / "chunks_manifest.json"
    run_report_path = chunks_dir / "chunk_run_report.json"
    chunks_dir_resolved = chunks_dir.resolve()

    fs_fail: list[str] = []
    manifest_fail: list[str] = []
    run_report_fail: list[str] = []
    run_report_warn: list[str] = []

    chunk_name_re = re.compile(r"^ch_(\d{2,3})_chunk_(\d{3})\.txt$")
    chunk_files: list[Path] = []
    fs_files: set[str] = set()
    chapters_fs: set[int] = set()

    if not chunks_dir.is_dir():
        fs_fail.append("CHUNKS_DIR_MISSING")
    else:
        chunk_files = sorted(chunks_dir.glob("ch_*_chunk_*.txt"))
        if not chunk_files:
            fs_fail.append("CHUNKS_EMPTY")
        else:
            invalid = [p.name for p in chunk_files if not chunk_name_re.match(p.name)]
            if invalid:
                fs_fail.append("CHUNK_FILENAME_INVALID")
            for p in chunk_files:
                m = chunk_name_re.match(p.name)
                if m:
                    chapters_fs.add(int(m.group(1)))
            fs_files = {p.name for p in chunk_files}

    total_fs = len(chunk_files) if chunk_files else 0
    first = chunk_files[0].name if chunk_files else "-"
    last = chunk_files[-1].name if chunk_files else "-"
    chunk_files_sample = ", ".join([p.name for p in chunk_files[:3]]) if chunk_files else "-"

    manifest_files: set[str] = set()
    total_manifest = 0
    manifest_schema_version = "MISSING"
    manifest_book_code = "-"
    manifest_lang = "-"
    if not manifest_path.is_file():
        manifest_fail.append("MANIFEST_MISSING")
    else:
        try:
            manifest = _load_json(manifest_path)
        except Exception:
            manifest_fail.append("MANIFEST_INVALID_JSON")
            manifest_schema_version = "INVALID_SHAPE"
        else:
            if not isinstance(manifest, dict):
                manifest_fail.append("MANIFEST_INVALID_SHAPE")
                manifest_schema_version = "INVALID_SHAPE"
            else:
                manifest_schema_version = str(manifest.get("schema_version", "")).strip() or "-"
                manifest_book_code = str(manifest.get("book_code", "")).strip() or "-"
                manifest_lang = str(manifest.get("lang", "")).strip() or "-"
                if manifest_schema_version != "chunks_manifest_v2":
                    manifest_fail.append("MANIFEST_SCHEMA_OUTDATED")
                if manifest_book_code != book_code:
                    manifest_fail.append("MANIFEST_BOOK_CODE_MISMATCH")
                if manifest_lang != "en":
                    manifest_fail.append("MANIFEST_LANG_MISMATCH")
                if not str(manifest.get("normalized_sha256", "")).strip():
                    manifest_fail.append("MANIFEST_NORMALIZED_SHA256_MISSING")

                chapters = manifest.get("chapters")
                if not isinstance(chapters, list) or not chapters:
                    manifest_fail.append("MANIFEST_CHAPTERS_MISSING")
                else:
                    chapter_ids: list[int] = []
                    for chapter in chapters:
                        if not isinstance(chapter, dict):
                            manifest_fail.append("MANIFEST_INVALID_CHAPTER_ITEM")
                            continue
                        try:
                            chapter_id = int(chapter.get("chapter_id"))
                        except Exception:
                            chapter_id = None
                        if chapter_id is None:
                            manifest_fail.append("MANIFEST_CHAPTER_ID_INVALID")
                        else:
                            chapter_ids.append(chapter_id)

                        chunks = chapter.get("chunks")
                        if not isinstance(chunks, list):
                            chunks = []
                        for chunk in chunks:
                            if not isinstance(chunk, dict):
                                manifest_fail.append("MANIFEST_INVALID_CHUNK_ITEM")
                                continue
                            total_manifest += 1
                            file_path = chunk.get("file_path")
                            if not file_path:
                                manifest_fail.append("MANIFEST_FILE_PATH_MISSING")
                                continue
                            manifest_files.add(str(file_path))

                    if chapter_ids:
                        expected = list(range(1, len(chapter_ids) + 1))
                        if sorted(set(chapter_ids)) != expected:
                            manifest_fail.append("MANIFEST_CHAPTER_ID_SEQUENCE")
                    else:
                        manifest_fail.append("MANIFEST_CHAPTERS_EMPTY")

                if manifest_files or fs_files:
                    if manifest_files != fs_files:
                        manifest_fail.append("MANIFEST_FILES_MISMATCH")
                if total_fs and total_manifest != total_fs:
                    manifest_fail.append("MANIFEST_TOTAL_MISMATCH")

    if not run_report_path.is_file():
        run_report_warn.append("RUN_REPORT_MISSING")
    else:
        try:
            report = _load_json(run_report_path)
        except Exception:
            run_report_fail.append("RUN_REPORT_INVALID_JSON")
        else:
            if not isinstance(report, dict):
                run_report_fail.append("RUN_REPORT_INVALID_SHAPE")
            else:
                checks = report.get("checks")
                if not isinstance(checks, dict):
                    checks = {}
                if checks.get("check_ok") is not True:
                    run_report_fail.append("RUN_REPORT_CHECK_NOT_OK")
                if "coverage_chars_ok" in checks and checks.get("coverage_chars_ok") is not True:
                    run_report_fail.append("RUN_REPORT_COVERAGE_CHARS_NOT_OK")
                if "single_chapter_mode" not in report:
                    run_report_warn.append("RUN_REPORT_SINGLE_CHAPTER_MODE_MISSING")

    def _fmt_reasons(reasons: list[str]) -> str:
        return "+".join(sorted(set(reasons)))

    manifest_status = "v2_ok" if not manifest_fail else f"fail({_fmt_reasons(manifest_fail)})"
    if run_report_fail:
        run_report_status = f"fail({_fmt_reasons(run_report_fail)})"
    elif run_report_warn:
        run_report_status = f"warn({_fmt_reasons(run_report_warn)})"
    else:
        run_report_status = "ok"

    if fs_fail or manifest_fail or run_report_fail:
        status = "FAIL"
    elif run_report_warn:
        status = "WARN"
    else:
        status = "OK"

    print(
        f"{book_code}: {status} — total={total_fs}, chapters={len(chapters_fs)}, "
        f"first={first}, last={last}, manifest={manifest_status}, run_report={run_report_status}"
    )
    print(
        "  evidence: "
        f"chunks_dir={chunks_dir_resolved}, "
        f"manifest_path={manifest_path}, "
        f"run_report_path={run_report_path}, "
        f"chunk_files_count={total_fs}, "
        f"chunk_files_sample_first3={chunk_files_sample}"
    )
    print(
        "  evidence: "
        f"manifest_schema_version={manifest_schema_version}, "
        f"manifest_book_code={manifest_book_code}, "
        f"manifest_lang={manifest_lang}, "
        f"run_report_present={run_report_path.is_file()}"
    )
    return status


def _check_normalized(book: str) -> str:
    book_id = _parse_book_id(book)
    book_code = f"book_{book_id:04d}"
    lang = "en"
    base_dir = Path("data/normalized") / book_code / lang
    normalized_path = base_dir / f"{book_code}_{lang}_v2.txt"
    report_path = base_dir / "normalize_report.json"
    preview_path = base_dir / "normalize_preview.txt"

    fail: list[str] = []
    warn: list[str] = []

    if not normalized_path.exists():
        fail.append("NORMALIZED_MISSING")
        norm_status = "missing"
    elif normalized_path.stat().st_size == 0:
        fail.append("NORMALIZED_EMPTY")
        norm_status = "empty"
    else:
        norm_status = "ok"

    report_status = "missing"
    if not report_path.exists():
        fail.append("REPORT_MISSING")
    else:
        try:
            report = _load_json(report_path)
        except Exception:
            fail.append("REPORT_INVALID_JSON")
            report_status = "invalid"
        else:
            if not isinstance(report, dict):
                fail.append("REPORT_INVALID_SHAPE")
                report_status = "invalid"
            else:
                status = str(report.get("status", "")).strip().upper()
                if status != "OK":
                    fail.append("REPORT_STATUS_NOT_OK")
                    report_status = f"status_{status or 'missing'}"
                else:
                    report_status = "ok"

    if not preview_path.exists():
        warn.append("PREVIEW_MISSING")
        preview_status = "missing"
    else:
        preview_status = "ok"

    if fail:
        status = "FAIL"
    elif warn:
        status = "WARN"
    else:
        status = "OK"

    print(
        f"{book_code}: {status} — normalized={norm_status}, "
        f"report={report_status}, preview={preview_status}"
    )
    return status






def _normalize_books_arg(books: Optional[List[str]]) -> List[str]:
    if not books:
        return []
    cleaned: list[str] = []
    for item in books:
        value = str(item).strip()
        if value:
            cleaned.append(value)
    return cleaned


def _parse_ignore_books(value: Optional[str]) -> set[str]:
    if not value:
        return set()
    parts = [item.strip() for item in value.split(",") if item.strip()]
    return set(parts)


def _apply_book_scope(
    candidates: List[str],
    *,
    ignore_books: set[str],
) -> tuple[List[str], List[str]]:
    if not ignore_books:
        return candidates, []
    selected = [b for b in candidates if b not in ignore_books]
    skipped = [b for b in candidates if b in ignore_books]
    return selected, skipped


def _expand_checks(checks: List[str]) -> List[str]:
    if not checks:
        return []
    expanded: List[str] = []
    for raw in checks:
        name = raw.strip()
        if not name:
            continue
        if name == "all":
            expanded.extend(ALL_CHECKS)
        else:
            expanded.append(name)
    seen: set[str] = set()
    result: List[str] = []
    for name in expanded:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _run_checks(
    books: Optional[List[str]],
    checks: List[str],
    *,
    strict: bool,
    langs: Optional[List[str]],
    ignore_books: set[str],
    allow_legacy: bool,
) -> int:
    status = True
    books_list = _normalize_books_arg(books)
    if not books_list:
        books_list = [CANONICAL_BOOK]
    if not allow_legacy:
        legacy = [book for book in books_list if book != CANONICAL_BOOK]
        if legacy:
            print(f"[FAIL] scope: legacy books not allowed: {', '.join(legacy)}")
            print("[INFO] use --allow-legacy to override")
            return 1
    for check in checks:
        if check == "chunks":
            chunk_books, skipped = _apply_book_scope(books_list, ignore_books=ignore_books)
            for book_code in skipped:
                print(f"{book_code}: SKIP — ignored")
            if not chunk_books:
                print("[SUMMARY] chunks: SKIP — books=0, ok=0, warn=0, fail=0")
                continue

            ok_count = 0
            warn_count = 0
            fail_count = 0
            for book_code in chunk_books:
                result = _check_chunks(book_code)
                if result == "FAIL":
                    fail_count += 1
                elif result == "WARN":
                    warn_count += 1
                else:
                    ok_count += 1
            if fail_count:
                summary = "FAIL"
                status = False
            elif warn_count:
                summary = "WARN"
            else:
                summary = "OK"
            print(
                f"[SUMMARY] chunks: {summary} — books={len(chunk_books)}, "
                f"ok={ok_count}, warn={warn_count}, fail={fail_count}"
            )
        elif check == "normalized":
            norm_books, skipped = _apply_book_scope(books_list, ignore_books=ignore_books)
            for book_code in skipped:
                print(f"{book_code}: SKIP — ignored")
            if not norm_books:
                print("[SUMMARY] normalized: SKIP — books=0, ok=0, warn=0, fail=0")
                continue

            ok_count = 0
            warn_count = 0
            fail_count = 0
            for book_code in norm_books:
                result = _check_normalized(book_code)
                if result == "FAIL":
                    fail_count += 1
                elif result == "WARN":
                    warn_count += 1
                else:
                    ok_count += 1
            if fail_count:
                summary = "FAIL"
                status = False
            elif warn_count:
                summary = "WARN"
            else:
                summary = "OK"
            print(
                f"[SUMMARY] normalized: {summary} — books={len(norm_books)}, "
                f"ok={ok_count}, warn={warn_count}, fail={fail_count}"
            )
        else:
            print(f"[FAIL] check desconhecido: {check}")
            status = False
    return 0 if status else 1


def run_checks(
    books: Optional[List[str]],
    checks: List[str],
    *,
    strict: bool = False,
    langs: Optional[List[str]] = None,
    ignore_books: Optional[set[str]] = None,
    allow_legacy: bool = False,
) -> int:
    expanded = _expand_checks(checks)
    return _run_checks(
        books,
        expanded,
        strict=strict,
        langs=langs,
        ignore_books=ignore_books or set(),
        allow_legacy=allow_legacy,
    )


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--book", type=str, action="append", default=None)
    parser.add_argument("--only-book", type=str, default=None)
    parser.add_argument("--ignore-books", type=str, default=None)
    parser.add_argument("--check", type=str, action="append", default=[])
    parser.add_argument("--strict", action="store_true", default=False)
    parser.add_argument("--langs", type=str, default=None)
    parser.add_argument("--allow-legacy", action="store_true", default=False)
    args = parser.parse_args()

    checks = args.check
    langs = args.langs.split(",") if args.langs else None
    ignore_books = _parse_ignore_books(args.ignore_books)
    if args.only_book and args.book:
        raise SystemExit("Use --only-book or --book (not both).")
    if args.only_book and args.only_book in ignore_books:
        raise SystemExit("--only-book cannot be in --ignore-books.")
    checks = _expand_checks(checks)
    if not checks:
        print("[INFO] use --check all|chunks|normalized")
        return 0
    if args.only_book:
        books = [args.only_book]
    else:
        books = args.book
    return _run_checks(
        books,
        checks,
        strict=args.strict,
        langs=langs,
        ignore_books=ignore_books,
        allow_legacy=args.allow_legacy,
    )


if __name__ == "__main__":
    sys.exit(main())
