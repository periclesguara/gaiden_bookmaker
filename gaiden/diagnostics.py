from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import tempfile
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

DB_PATH = Path("data/db/gaiden.sqlite3")
CHUNKS_ROOT = Path("data/chunks")
CONTRACTS_ROOT = Path("gaiden/contracts")
TRANSLATED_ROOT = Path("data/translated")

ALLOWED_LANG_DIRS = {"DE", "EN", "ES", "PT-BR", "IT", "FR"}
ALL_CHECKS = [
    "chunks",
    "contracts",
    "translate_mapping",
    "merge_plan",
    "merge_presence",
    "split_stage_refs",
    "golden_snapshot",
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


def _iter_contracts() -> Iterable[Path]:
    if not CONTRACTS_ROOT.is_dir():
        return []
    return sorted(CONTRACTS_ROOT.glob("*.json"))


def _load_json(path: Path) -> Dict[str, object]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _looks_like_book_dir(path: str) -> bool:
    return bool(re.search(r"data/chunks/book_\d{4}/en", path))


def _looks_like_translated_dir(path: str) -> bool:
    return bool(re.search(r"data/translated/book_\d{4}/[A-Z-]+", path))


def _is_placeholder_path(path: str) -> bool:
    return "<BOOK_ID>" in path or "{BOOK_ID}" in path


def _check_chunks(book: str) -> bool:
    book_id = _parse_book_id(book)
    book_code = f"book_{book_id:04d}"
    chunks_dir = CHUNKS_ROOT / book_code / "en"
    manifest_path = chunks_dir / "chunks_manifest.json"
    run_report_path = chunks_dir / "chunk_run_report.json"

    fs_fail: list[str] = []
    manifest_fail: list[str] = []
    run_report_fail: list[str] = []
    run_report_warn: list[str] = []

    chunk_name_re = re.compile(r"^ch_(\d{2,3})_chunk_(\d{3})\.txt$")
    files: list[Path] = []
    fs_files: set[str] = set()
    chapters_fs: set[int] = set()

    if not chunks_dir.is_dir():
        fs_fail.append("CHUNKS_DIR_MISSING")
    else:
        files = sorted(chunks_dir.glob("ch_*_chunk_*.txt"))
        if not files:
            fs_fail.append("CHUNKS_EMPTY")
        else:
            invalid = [p.name for p in files if not chunk_name_re.match(p.name)]
            if invalid:
                fs_fail.append("CHUNK_FILENAME_INVALID")
            for p in files:
                m = chunk_name_re.match(p.name)
                if m:
                    chapters_fs.add(int(m.group(1)))
            fs_files = {p.name for p in files}

    total_fs = len(files) if files else 0
    first = files[0].name if files else "-"
    last = files[-1].name if files else "-"

    manifest_files: set[str] = set()
    total_manifest = 0
    if not manifest_path.is_file():
        manifest_fail.append("MANIFEST_MISSING")
    else:
        try:
            manifest = _load_json(manifest_path)
        except Exception:
            manifest_fail.append("MANIFEST_INVALID_JSON")
        else:
            if not isinstance(manifest, dict):
                manifest_fail.append("MANIFEST_INVALID_SHAPE")
            else:
                if str(manifest.get("schema_version", "")).strip() != "chunks_manifest_v2":
                    manifest_fail.append("MANIFEST_SCHEMA_OUTDATED")
                if str(manifest.get("book_code", "")).strip() != book_code:
                    manifest_fail.append("MANIFEST_BOOK_CODE_MISMATCH")
                if str(manifest.get("lang", "")).strip() != "en":
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
        ok = False
    elif run_report_warn:
        status = "WARN"
        ok = True
    else:
        status = "OK"
        ok = True

    print(
        f"{book_code}: {status} — total={total_fs}, chapters={len(chapters_fs)}, "
        f"first={first}, last={last}, manifest={manifest_status}, run_report={run_report_status}"
    )
    return ok


def _check_contracts() -> bool:
    ok = True
    for path in _iter_contracts():
        data = _load_json(path)
        model = str(data.get("model", "")).strip()
        chunk_dir = str(data.get("chunk_dir", "")).strip()
        out_dir = str(data.get("out_dir", "")).strip()
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        language = str(output.get("language", "")).strip()

        if model != "gpt-5.2":
            print(f"[FAIL] contracts: {path.name} model={model}")
            ok = False

        if "split" in chunk_dir.lower():
            print(f"[FAIL] contracts: {path.name} chunk_dir contém split")
            ok = False

        if not (chunk_dir and (_looks_like_book_dir(chunk_dir) or _is_placeholder_path(chunk_dir))):
            print(f"[FAIL] contracts: {path.name} chunk_dir inválido: {chunk_dir}")
            ok = False

        if not (out_dir and (_looks_like_translated_dir(out_dir) or _is_placeholder_path(out_dir))):
            print(f"[FAIL] contracts: {path.name} out_dir inválido: {out_dir}")
            ok = False

        if not language:
            print(f"[FAIL] contracts: {path.name} output.language ausente")
            ok = False
        elif language.upper() not in {l.replace("-", "") for l in ALLOWED_LANG_DIRS} and language.lower() not in {l.lower() for l in ALLOWED_LANG_DIRS}:
            print(f"[WARN] contracts: {path.name} language inesperada: {language}")

    if ok:
        print(f"[OK] contracts: {len(list(_iter_contracts()))} contratos validados")
    return ok


def _check_translate_mapping(book: str) -> bool:
    book_id = _parse_book_id(book)
    sample = "ch_01_chunk_001.txt"
    ok = True

    for path in _iter_contracts():
        data = _load_json(path)
        chunk_dir = str(data.get("chunk_dir", "")).strip()
        out_dir = str(data.get("out_dir", "")).strip()
        if not out_dir:
            continue

        out_dir_path = Path(out_dir)
        lang_dir = out_dir_path.name
        expected = out_dir_path / f"{Path(sample).stem}.{lang_dir}.txt"
        if not expected.name.endswith(f".{lang_dir}.txt"):
            print(f"[FAIL] translate_mapping: {path.name} sufixo inválido")
            ok = False

        if "split" in chunk_dir.lower():
            print(f"[FAIL] translate_mapping: {path.name} chunk_dir contém split")
            ok = False

    if ok:
        print("[OK] translate_mapping: naming pattern válido")
    return ok


def _check_merge_plan(book: str, *, strict: bool, langs: Optional[List[str]]) -> bool:
    book_id = _parse_book_id(book)
    book_dir = TRANSLATED_ROOT / f"book_{book_id:04d}"
    if not book_dir.exists():
        if strict:
            print(f"[FAIL] merge_plan: diretório ausente {book_dir}")
            return False
        print(f"[WARN] merge_plan: diretório ausente {book_dir}")
        return True

    ok = True
    lang_filter = None
    if langs:
        lang_filter = {lang.strip().upper() for lang in langs if lang.strip()}
    for lang_dir in sorted(book_dir.iterdir()):
        if not lang_dir.is_dir():
            continue
        lang_key = lang_dir.name
        if lang_filter and lang_key.upper() not in lang_filter:
            continue
        pattern = f"ch_*_chunk_*.{lang_key}.txt"
        files = sorted(lang_dir.glob(pattern))
        if not files:
            if strict:
                print(f"[FAIL] merge_plan: {lang_key} sem chunks")
                ok = False
            else:
                print(f"[WARN] merge_plan: {lang_key} sem chunks")
            continue
        merge_name = f"merge_translate_{lang_key}.txt"
        print(f"[OK] merge_plan: {lang_key} -> {merge_name} ({len(files)} chunks)")

    return ok


def _check_merge_presence(book: str, *, strict: bool, langs: Optional[List[str]]) -> bool:
    book_id = _parse_book_id(book)
    book_dir = TRANSLATED_ROOT / f"book_{book_id:04d}"
    if not book_dir.exists():
        if strict:
            print(f"[FAIL] merge_presence: diretório ausente {book_dir}")
            return False
        print(f"[WARN] merge_presence: diretório ausente {book_dir}")
        return True

    ok = True
    lang_filter = None
    if langs:
        lang_filter = {lang.strip().upper() for lang in langs if lang.strip()}
    for lang_dir in sorted(book_dir.iterdir()):
        if not lang_dir.is_dir():
            continue
        lang_key = lang_dir.name
        if lang_filter and lang_key.upper() not in lang_filter:
            continue
        merge_path = lang_dir / f"merge_translate_{lang_key}.txt"
        if not merge_path.exists():
            if strict:
                print(f"[FAIL] merge_presence: {lang_key} sem merge_translate")
                ok = False
            else:
                print(f"[WARN] merge_presence: {lang_key} sem merge_translate")
            continue
        print(f"[OK] merge_presence: {lang_key} -> {merge_path}")
    return ok


def _iter_files_for_scan(root: Path) -> Iterable[Path]:
    skip_dirs = {".git", ".venv", "node_modules", "__pycache__"}
    for path in root.rglob("*"):
        if any(part in skip_dirs for part in path.parts):
            continue
        posix_path = path.as_posix()
        if "/migrations/" in posix_path:
            continue
        if posix_path.startswith("data/") or "/data/" in posix_path:
            continue
        if posix_path.startswith("scripts/") or "/scripts/" in posix_path:
            continue
        if "gaiden_bookmaker.egg-info" in posix_path:
            continue
        if posix_path.endswith("gaiden/diagnostics.py"):
            continue
        if path.is_file():
            yield path


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(1024)
        return b"\x00" in chunk
    except OSError:
        return True


def _check_split_stage_refs() -> bool:
    forbidden_patterns = [
        r"split_01",
        r"split_chapters",
        r"editorial_split",
        r"split_stage",
        r"split_step",
        r"split stage",
        r"stage\s+SPLIT",
        r"data/chunks/.*/split",
    ]
    combined = re.compile("|".join(forbidden_patterns), re.IGNORECASE)
    root = Path(".")
    ok = True
    for path in _iter_files_for_scan(root):
        if _is_binary(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if combined.search(text):
            print(f"[FAIL] split_stage_refs: {path}")
            ok = False
    if ok:
        print("[OK] split_stage_refs: nenhum resíduo de split stage")
    return ok


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _check_golden_snapshot(langs: Optional[List[str]]) -> bool:
    from gaiden.translate import run_translate_with_contract

    lang_list = [lang.strip().upper() for lang in (langs or ["FR", "IT"]) if lang.strip()]
    if not lang_list:
        lang_list = ["FR", "IT"]

    temp_root = Path(tempfile.mkdtemp(prefix="gaiden_golden_"))
    try:
        chunk_dir = temp_root / "book_0001" / "en"
        _write_text(chunk_dir / "ch_01_chunk_001.txt", "Chunk one.\n")
        _write_text(chunk_dir / "ch_01_chunk_002.txt", "Chunk two.\n")

        ok = True
        for lang_key in lang_list:
            out_dir = temp_root / "book_0001" / lang_key
            contract_path = temp_root / f"contract_{lang_key}.json"
            contract = {
                "name": f"Golden Snapshot {lang_key}",
                "model": "gpt-5.2",
                "temperature": 0.4,
                "max_output_tokens": 1200,
                "chunk_dir": str(chunk_dir),
                "out_dir": str(out_dir),
                "output": {"language": lang_key.lower()},
                "system_prompt": "DRY RUN",
                "user_prompt": "{text}",
            }
            contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")

            run_translate_with_contract(
                contract_path,
                dry_run=True,
                limit_chunks=2,
                chunk_dir_override=chunk_dir,
                out_dir_override=out_dir,
            )

            out_1 = out_dir / f"ch_01_chunk_001.{lang_key}.txt"
            out_2 = out_dir / f"ch_01_chunk_002.{lang_key}.txt"
            merged = out_dir / f"merge_translate_{lang_key}.txt"
            if not out_1.exists() or not out_2.exists():
                print(f"[FAIL] golden_snapshot: {lang_key} chunks ausentes")
                ok = False
                continue
            if not merged.exists():
                print(f"[FAIL] golden_snapshot: {lang_key} merge ausente")
                ok = False
                continue
            merged_text = merged.read_text(encoding="utf-8")
            if "chunk_001" not in merged_text or "chunk_002" not in merged_text:
                print(f"[FAIL] golden_snapshot: {lang_key} merge sem ordem correta")
                ok = False
            else:
                print(f"[OK] golden_snapshot: {lang_key}")

        return ok
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


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
    book: Optional[str],
    checks: List[str],
    *,
    strict: bool,
    langs: Optional[List[str]],
) -> int:
    status = True
    for check in checks:
        if check == "chunks":
            if not book:
                print("[FAIL] chunks: --book obrigatório")
                status = False
            else:
                status = _check_chunks(book) and status
        elif check == "contracts":
            status = _check_contracts() and status
        elif check == "translate_mapping":
            if not book:
                print("[FAIL] translate_mapping: --book obrigatório")
                status = False
            else:
                status = _check_translate_mapping(book) and status
        elif check == "merge_plan":
            if not book:
                print("[FAIL] merge_plan: --book obrigatório")
                status = False
            else:
                status = _check_merge_plan(book, strict=strict, langs=langs) and status
        elif check == "merge_presence":
            if not book:
                print("[FAIL] merge_presence: --book obrigatório")
                status = False
            else:
                status = _check_merge_presence(book, strict=strict, langs=langs) and status
        elif check == "split_stage_refs":
            status = _check_split_stage_refs() and status
        elif check == "golden_snapshot":
            status = _check_golden_snapshot(langs) and status
        else:
            print(f"[FAIL] check desconhecido: {check}")
            status = False
    return 0 if status else 1


def run_checks(
    book: Optional[str],
    checks: List[str],
    *,
    strict: bool = False,
    langs: Optional[List[str]] = None,
) -> int:
    expanded = _expand_checks(checks)
    return _run_checks(book, expanded, strict=strict, langs=langs)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--book", type=str, default=None)
    parser.add_argument("--check", type=str, action="append", default=[])
    parser.add_argument("--strict", action="store_true", default=False)
    parser.add_argument("--langs", type=str, default=None)
    parser.add_argument("--stage", type=str, default=None)
    parser.add_argument("--offline", action="store_true", default=False)
    args = parser.parse_args()

    checks = args.check
    langs = args.langs.split(",") if args.langs else None
    if args.stage == "translate" and args.offline:
        checks = [
            "chunks",
            "contracts",
            "translate_mapping",
            "split_stage_refs",
            "golden_snapshot",
        ]
    checks = _expand_checks(checks)
    if not checks:
        print(
            "[INFO] use --check all|chunks|contracts|translate_mapping|merge_plan|merge_presence|split_stage_refs|golden_snapshot"
        )
        return 0
    return _run_checks(args.book, checks, strict=args.strict, langs=langs)


if __name__ == "__main__":
    sys.exit(main())
