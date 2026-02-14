from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import json
from pathlib import Path
import os
import subprocess
import sys

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from gaiden.contracts_v2.resolver import resolve_translate_contract_path
from gaiden.lang import normalize_lang_code
from gaiden.raw_resolver import canonical_raw_dir, resolve_raw_source
from gaiden.translate_engine_v1 import run_translate_safe
from gaiden.tools.agent_translate_default import run_agent_translate
from gaiden.refine_split import process_language
from editorial.models import EditionPipeline, EditionText, PipelineStage, Edition
from pipeline.models import PipelineRun, PipelineRunItem
from pipeline.services import utils


def _parse_book_id(book_code: str) -> int | None:
    try:
        if book_code.startswith("book_"):
            return int(book_code.split("_", 1)[1])
    except (ValueError, IndexError):
        return None

    digits = "".join(ch for ch in book_code if ch.isdigit())
    try:
        return int(digits) if digits else None
    except ValueError:
        return None


def _lang_dir(lang: str) -> str:
    return normalize_lang_code(lang, default=utils.normalize_lang(lang))


def _lang_fs(lang: str) -> str:
    return normalize_lang_code(lang, default=utils.normalize_lang(lang))


def _lang_db_code(lang: str) -> str:
    norm = utils.normalize_lang(lang)
    return "pt-br" if norm == "ptbr" else norm


def _translate_paths(book_id: int, book_code: str, lang: str) -> tuple[Path, Path, Path]:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    chunk_dir = data_dir / "chunks" / book_code / "en"
    out_dir = data_dir / "translated" / f"book_{book_id:04d}" / _lang_dir(lang)
    merge_path = out_dir / "merge_refine_clean.txt"
    return chunk_dir, out_dir, merge_path


def _split_paths(book_id: int, lang: str) -> tuple[Path, Path, Path]:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    lang_dir = _lang_dir(lang)
    base_dir = data_dir / "translated" / f"book_{book_id:04d}" / lang_dir
    merge_path = base_dir / "merge_refine_clean.txt"
    split_dir = base_dir / "split_chapters_for_refine"
    return base_dir, merge_path, split_dir


def _normalized_path(book_code: str, lang: str) -> Path:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    lang_code = _lang_fs(lang)
    name_v2 = f"{book_code}_{lang_code}_v2.txt"
    return data_dir / "normalized" / book_code / lang_code / name_v2


def _normalized_report_path(book_code: str, lang: str) -> Path:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    lang_code = _lang_fs(lang)
    return data_dir / "normalized" / book_code / lang_code / "normalize_report.json"


def _normalized_preview_path(book_code: str, lang: str) -> Path:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    lang_code = _lang_fs(lang)
    return data_dir / "normalized" / book_code / lang_code / "normalize_preview.txt"


def _resolve_normalized_path(book_code: str, lang: str) -> Path | None:
    path = _normalized_path(book_code, lang)
    return path if path.exists() else None


def _resolve_raw_path(book_code: str, lang: str) -> tuple[Path | None, Path, str | None, Path | None]:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    canonical_dir = canonical_raw_dir(book_code, lang, data_dir)
    try:
        resolution = resolve_raw_source(
            book_code,
            lang,
            data_dir,
            create_alias=True,
            logger=None,
        )
        return resolution.raw_path, resolution.selected_dir, None, resolution.alias_created
    except ValueError as exc:
        if "INVALID_STATE" in str(exc):
            return None, canonical_dir, "INVALID_STATE", None
        raise
    except FileNotFoundError:
        return None, canonical_dir, "RAW_MISSING", None


def _chunks_dir(book_code: str, lang: str) -> Path:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    return data_dir / "chunks" / book_code / _lang_fs(lang)


def _chunks_manifest_path(chunks_dir: Path) -> Path:
    return chunks_dir / "chunks_manifest.json"


def _chunks_v2_status(chunks_dir: Path) -> tuple[bool, dict | None]:
    manifest_path = _chunks_manifest_path(chunks_dir)
    manifest = _read_json(manifest_path)
    if not manifest_path.exists():
        return False, manifest
    if not isinstance(manifest, dict):
        return False, manifest
    if manifest.get("schema_version") != "chunks_manifest_v2":
        return False, manifest
    return True, manifest


def _chunks_exist(chunks_dir: Path) -> bool:
    ok, _ = _chunks_v2_status(chunks_dir)
    return ok


def _remove_existing_chunks(chunks_dir: Path) -> None:
    if not chunks_dir.exists():
        return
    for path in chunks_dir.glob("ch_*_chunk_*.txt"):
        path.unlink()
    manifest = _chunks_manifest_path(chunks_dir)
    if manifest.exists():
        manifest.unlink()
    run_report = chunks_dir / "chunk_run_report.json"
    if run_report.exists():
        run_report.unlink()


def _resolve_contract_path(lang: str) -> Path:
    target_lang = normalize_lang_code(lang, default=utils.normalize_lang(lang))
    return resolve_translate_contract_path(target_lang)


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _run_module(log_file, module: str, args: list[str]) -> None:
    cmd = [sys.executable, "-m", module, *args]
    log_file.write(f"COMMAND: {' '.join(cmd)}\n")
    log_file.flush()
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        log_file.write(result.stdout)
    if result.stderr:
        log_file.write(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"{module} failed with exit code {result.returncode}")


def _chunk_token_limits() -> tuple[str, str]:
    target = os.getenv("GAIDEN_CHUNK_TARGET_TOKENS", "1500")
    max_t = os.getenv("GAIDEN_CHUNK_MAX_TOKENS", "2000")
    return target, max_t


def _normalized_status(book_code: str, lang: str) -> tuple[Path, dict | None, bool]:
    norm_path = _normalized_path(book_code, lang)
    report = _read_json(_normalized_report_path(book_code, lang))
    ok = (
        norm_path.exists()
        and norm_path.stat().st_size > 0
        and isinstance(report, dict)
        and report.get("status") == "OK"
    )
    return norm_path, report, ok


def _run_precheck(
    *,
    book_code: str,
    lang: str,
    log_path: Path,
    ensure_normalized: bool,
    ensure_chunks: bool,
    allow_run: bool,
) -> dict:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    chunk_lang = "en"

    def _log(log_file, message: str) -> None:
        log_file.write(message + "\n")

    with log_path.open("w", encoding="utf-8") as log_file:
        _log(log_file, "PRECHECK: start")
        raw_reason = None
        raw_path = None
        raw_dir = canonical_raw_dir(book_code, lang, data_dir)
        try:
            resolution = resolve_raw_source(
                book_code,
                lang,
                data_dir,
                create_alias=True,
                logger=lambda msg: _log(log_file, msg),
            )
            raw_path = resolution.raw_path
            raw_dir = resolution.selected_dir
        except ValueError as exc:
            if "INVALID_STATE" in str(exc):
                raw_reason = "INVALID_STATE"
            else:
                raise
            _log(log_file, f"RAW_DIR_SELECTED: {raw_dir}")
        except FileNotFoundError:
            raw_reason = "RAW_MISSING"
            _log(log_file, f"RAW_DIR_SELECTED: {raw_dir}")

        norm_path, _norm_report, norm_ok = _normalized_status(book_code, lang)
        _log(log_file, f"NORMALIZED_PRESENT: {norm_ok}")

        if ensure_normalized and not norm_ok:
            if raw_reason:
                _log(log_file, "PRECHECK: normalize skipped (raw missing/invalid)")
            elif allow_run:
                _log(log_file, "AUTO: normalize (precheck)")
                _run_module(log_file, "gaiden.normalize", [book_code, _lang_fs(lang)])
                norm_path, _norm_report, norm_ok = _normalized_status(book_code, lang)
                _log(log_file, f"NORMALIZED_PRESENT: {norm_ok}")
            else:
                _log(log_file, "PRECHECK: normalize required")

        chunk_dir = _chunks_dir(book_code, chunk_lang)
        chunks_ok, _manifest = _chunks_v2_status(chunk_dir)
        _log(log_file, f"CHUNKS_V2_PRESENT: {chunks_ok}")

        if ensure_chunks and not chunks_ok:
            if not norm_ok:
                _log(log_file, "PRECHECK: chunk skipped (normalized missing/invalid)")
            elif allow_run:
                _log(log_file, "AUTO: chunk (precheck)")
                target_tokens, max_tokens = _chunk_token_limits()
                _run_module(
                    log_file,
                    "gaiden.chunk_book",
                    [
                        "--book",
                        book_code,
                        "--lang",
                        chunk_lang,
                        "--normalized",
                        str(norm_path),
                        "--out",
                        str(chunk_dir),
                        "--target-tokens",
                        target_tokens,
                        "--max-tokens",
                        max_tokens,
                    ],
                )
                chunks_ok, _manifest = _chunks_v2_status(chunk_dir)
                _log(log_file, f"CHUNKS_V2_PRESENT: {chunks_ok}")
            else:
                _log(log_file, "PRECHECK: chunk required")

        _log(log_file, "PRECHECK: end")

    return {
        "raw_reason": raw_reason,
        "raw_path": raw_path,
        "raw_dir": raw_dir,
        "normalized_path": norm_path,
        "normalized_ok": norm_ok,
        "chunk_dir": chunk_dir,
        "chunks_ok": chunks_ok,
    }


def _run_normalize_dependency(
    *,
    book_code: str,
    lang: str,
    edition: Edition,
    pipeline_state: EditionPipeline,
    log_file,
) -> Path:
    norm_path, report, norm_ok = _normalized_status(book_code, lang)
    if norm_ok:
        return norm_path

    raw_path, raw_dir, raw_reason, raw_alias = _resolve_raw_path(book_code, lang)
    if raw_reason:
        raise RuntimeError(f"RAW_{raw_reason}")

    had_existing = norm_path.exists()
    log_file.write(f"RAW_DIR_SELECTED: {raw_dir}\n")
    if raw_alias:
        log_file.write(f"RAW_ALIAS_CREATED: {raw_alias}\n")
    log_file.write(f"RAW_SOURCE_FOUND: {raw_path}\n")
    log_file.write(f"OUTPUT: {norm_path}\n")
    if had_existing:
        log_file.write("OVERWRITTEN: existing output replaced\n")
    _run_module(log_file, "gaiden.normalize", [book_code, _lang_fs(lang)])
    report = _read_json(_normalized_report_path(book_code, lang))
    if report:
        log_file.write(f"NORMALIZE_CHECK: {report.get('status', 'FAIL')}\n")
        if report.get("check_fail_reasons"):
            log_file.write(
                "NORMALIZE_CHECK_REASONS: " + "; ".join(report["check_fail_reasons"]) + "\n"
            )
    if report and report.get("status") == "FAIL":
        raise RuntimeError("Normalize check failed during dependency.")
    if not norm_path.exists():
        raise FileNotFoundError(f"Normalized output not found: {norm_path}")

    texts, _ = EditionText.objects.get_or_create(edition=edition)
    texts.normalized_path = str(norm_path)
    texts.normalized_text = ""
    texts.save(update_fields=["normalized_path", "normalized_text", "updated_at"])
    pipeline_state.normalized_at = timezone.now()
    pipeline_state.current_stage = PipelineStage.NORMALIZED
    pipeline_state.save(update_fields=["normalized_at", "current_stage"])

    return norm_path


def _run_chunk_dependency(
    *,
    book_code: str,
    norm_path: Path,
    log_file,
    skip_existing: bool,
) -> tuple[Path, bool]:
    chunk_lang = "en"
    chunk_dir = _chunks_dir(book_code, chunk_lang)
    chunks_ok, manifest = _chunks_v2_status(chunk_dir)
    if chunks_ok and skip_existing:
        log_file.write("SKIP: chunks exist\n")
        log_file.write("CHUNK_SHARED_LANG: en\n")
        log_file.write("NOTE: Chunking is shared; forced to EN\n")
        return chunk_dir, False

    had_existing = chunk_dir.exists()
    if had_existing:
        _remove_existing_chunks(chunk_dir)

    target_tokens, max_tokens = _chunk_token_limits()
    log_file.write("CHUNK_SHARED_LANG: en\n")
    log_file.write("NOTE: Chunking is shared; forced to EN\n")
    log_file.write(f"TARGET_TOKENS: {target_tokens}\n")
    log_file.write(f"MAX_TOKENS: {max_tokens}\n")
    if had_existing:
        log_file.write("OVERWRITTEN: existing output replaced\n")
    _run_module(
        log_file,
        "gaiden.chunk_book",
        [
            "--book",
            book_code,
            "--lang",
            chunk_lang,
            "--normalized",
            str(norm_path),
            "--out",
            str(chunk_dir),
            "--target-tokens",
            target_tokens,
            "--max-tokens",
            max_tokens,
        ],
    )
    manifest = _read_json(_chunks_manifest_path(chunk_dir))
    if manifest:
        log_file.write(f"CHUNK_CHECK: {'OK' if manifest.get('check_ok') else 'FAIL'}\n")
        if manifest.get("check_fail_reasons"):
            log_file.write("CHUNK_CHECK_REASONS: " + "; ".join(manifest["check_fail_reasons"]) + "\n")
    if not _chunks_exist(chunk_dir):
        raise FileNotFoundError(f"Chunks not found: {chunk_dir}")
    if manifest and manifest.get("check_ok") is False:
        raise RuntimeError("Chunk check failed during dependency.")

    return chunk_dir, True


class Command(BaseCommand):
    help = "Run MATRIX pipeline queue (sequential, translate-only for MVP)."

    def add_arguments(self, parser):
        parser.add_argument("run_id", nargs="?", type=int, help="PipelineRun id")
        parser.add_argument("--book", type=str, default=None, help="book code (book_0003)")
        parser.add_argument("--lang", type=str, default=None, help="language (en, es, ptbr, ...)")
        parser.add_argument(
            "--stage",
            type=str,
            default=None,
            choices=["precheck", "normalize", "chunk"],
            help="run a single stage without DB",
        )

    def handle(self, *args, **options):
        run_id = options["run_id"]
        stage = options.get("stage")
        book_code = options.get("book")
        lang = options.get("lang")

        if run_id is None and stage:
            if not book_code or not lang:
                raise CommandError("--book and --lang are required for --stage")
            log_dir = Path(settings.BASE_DIR).parent / "docs" / "audit" / "runs" / "adhoc"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{book_code}_{lang}_{stage}.log"
            ensure_chunks = stage in {"chunk"}
            allow_run = stage in {"normalize", "chunk"}
            _run_precheck(
                book_code=book_code,
                lang=lang,
                log_path=log_path,
                ensure_normalized=True,
                ensure_chunks=ensure_chunks,
                allow_run=allow_run,
            )
            self.stdout.write(self.style.SUCCESS(f"[OK] {stage} precheck complete"))
            self.stdout.write(self.style.NOTICE(f"Log: {log_path}"))
            return
        if run_id is None:
            raise CommandError("run_id é obrigatório quando --stage não é usado.")
        run = PipelineRun.objects.get(pk=run_id)

        updated = PipelineRun.objects.filter(pk=run_id, status="PENDING").update(
            status="RUNNING",
            started_at=timezone.now(),
        )
        if updated == 0:
            self.stdout.write(self.style.WARNING(f"Run {run_id} não está em PENDING."))
            return

        run.refresh_from_db()

        opts = run.options or {}
        skip_existing = bool(opts.get("skip_existing", True))
        stop_on_error = bool(opts.get("stop_on_error", False))
        dry_run = bool(opts.get("dry_run", False))

        any_fail = False

        items = list(run.items.order_by("id"))
        for item in items:
            if item.status != "PENDING":
                continue

            item.started_at = timezone.now()
            item.status = "RUNNING"
            item.save(update_fields=["status", "started_at"])

            log_dir = Path(settings.BASE_DIR).parent / "docs" / "audit" / "runs" / str(run.id)
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{item.id}.log"
            item.log_path = str(log_path)
            item.save(update_fields=["log_path"])

            book_id = item.book_id or _parse_book_id(item.book_code or "")
            if book_id is None:
                with log_path.open("w", encoding="utf-8") as log_file:
                    log_file.write("COMMAND: n/a (invalid book_code/book_id)\n")
                    log_file.write("ERROR: INVALID_BOOK_CODE\n")
                item.status = "FAILED"
                item.finished_at = timezone.now()
                item.skipped_reason = "INVALID_STATE"
                item.save(update_fields=["status", "finished_at", "skipped_reason"])
                any_fail = True
                if stop_on_error:
                    break
                continue

            try:
                command_line = "n/a"
                book_code = item.book_code or f"book_{book_id:04d}"
                if run.action == "NORMALIZE":
                    norm_path = _normalized_path(book_code, item.lang)
                    item.out_path = str(norm_path)
                    item.save(update_fields=["out_path"])
                    command_line = f"python -m gaiden.normalize {book_code} {_lang_fs(item.lang)}"
                elif run.action == "CHUNK":
                    chunk_lang = "en"
                    chunk_dir = _chunks_dir(book_code, chunk_lang)
                    manifest_path = _chunks_manifest_path(chunk_dir)
                    item.out_path = str(manifest_path)
                    item.save(update_fields=["out_path"])
                    normalized_path = _resolve_normalized_path(book_code, chunk_lang)
                    target_tokens, max_tokens = _chunk_token_limits()
                    command_line = (
                        "python -m gaiden.chunk_book "
                        f"--book {book_code} --lang {chunk_lang} "
                        f"--normalized {normalized_path or 'MISSING'} "
                        f"--out {chunk_dir} --target-tokens {target_tokens} --max-tokens {max_tokens}"
                    )
                elif run.action == "TRANSLATE":
                    chunk_dir, out_dir, merge_path = _translate_paths(book_id, book_code, item.lang)
                    item.out_path = str(merge_path)
                    item.save(update_fields=["out_path"])

                    command_line = (
                        "run_translate_safe("
                        f"book_id={book_code}, chunk_dir={chunk_dir}, out_dir={out_dir}, "
                        f"suffix={_lang_dir(item.lang)}, contract={_resolve_contract_path(item.lang)})"
                    )
                elif run.action == "TRANSLATE_DEFAULT":
                    chunk_dir, out_dir, merge_path = _translate_paths(book_id, book_code, item.lang)
                    item.out_path = str(merge_path)
                    item.save(update_fields=["out_path"])
                    command_line = (
                        "run_agent_translate("
                        f"book_id={book_code}, chunk_dir={chunk_dir}, out_dir={out_dir}, "
                        f"suffix={_lang_dir(item.lang)}, agent=ALAMAGUEDERAZ)"
                    )
                elif run.action == "SPLIT_FOR_REFINE":
                    base_dir, merge_path, split_dir = _split_paths(book_id, item.lang)
                    item.out_path = str(split_dir)
                    item.save(update_fields=["out_path"])
                    command_line = (
                        "process_language("
                        f"book='book_{book_id:04d}', lang='{_lang_dir(item.lang)}', parts=2)"
                    )
                else:
                    raise ValueError(f"Unsupported action: {run.action}")

                precheck = None
                if run.action in {"NORMALIZE", "CHUNK", "TRANSLATE", "TRANSLATE_DEFAULT"}:
                    precheck = _run_precheck(
                        book_code=book_code,
                        lang=item.lang if run.action == "NORMALIZE" else "en",
                        log_path=log_path,
                        ensure_normalized=True,
                        ensure_chunks=(run.action in {"CHUNK", "TRANSLATE", "TRANSLATE_DEFAULT"}),
                        allow_run=False,
                    )
                    if precheck["raw_reason"]:
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write(f"SKIP: {precheck['raw_reason']}\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = precheck["raw_reason"]
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                if dry_run:
                    with log_path.open("a", encoding="utf-8") as log_file:
                        log_file.write(f"COMMAND: {command_line}\n")
                        log_file.write("DRY-RUN: no execution\n")
                    item.status = "SKIPPED"
                    item.skipped_reason = "INVALID_STATE"
                    item.finished_at = timezone.now()
                    item.save(update_fields=["status", "skipped_reason", "finished_at"])
                    continue

                if run.action == "NORMALIZE":
                    edition = Edition.objects.select_related("work", "language").filter(
                        work__code=book_code,
                        language__code=_lang_db_code(item.lang),
                    ).first()
                    if not edition:
                        raise FileNotFoundError("Edition not found for normalize.")

                    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)

                    raw_path = precheck["raw_path"] if precheck else None
                    raw_dir = precheck["raw_dir"] if precheck else None

                    norm_path, _report, norm_ok = _normalized_status(book_code, item.lang)
                    if skip_existing and norm_ok:
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: normalized exists\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = "NORMALIZED_EXISTS"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    had_existing = norm_path.exists()
                    with log_path.open("a", encoding="utf-8") as log_file:
                        if raw_dir:
                            log_file.write(f"RAW_DIR_SELECTED: {raw_dir}\n")
                        if raw_path:
                            log_file.write(f"RAW_SOURCE_FOUND: {raw_path}\n")
                        log_file.write(f"OUTPUT: {norm_path}\n")
                        if had_existing:
                            log_file.write("OVERWRITTEN: existing output replaced\n")
                        _run_module(log_file, "gaiden.normalize", [book_code, _lang_fs(item.lang)])
                        report = _read_json(_normalized_report_path(book_code, item.lang))
                        if report:
                            log_file.write(
                                f"NORMALIZE_CHECK: {report.get('status', 'FAIL')}\n"
                            )
                            if report.get("check_fail_reasons"):
                                log_file.write(
                                    "NORMALIZE_CHECK_REASONS: "
                                    + "; ".join(report["check_fail_reasons"])
                                    + "\n"
                                )

                    if not norm_path.exists():
                        raise FileNotFoundError(f"Normalized output not found: {norm_path}")
                    report = _read_json(_normalized_report_path(book_code, item.lang))
                    if report and report.get("status") == "FAIL":
                        item.status = "FAILED"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "finished_at"])
                        continue

                    texts, _ = EditionText.objects.get_or_create(edition=edition)
                    texts.normalized_path = str(norm_path)
                    texts.normalized_text = ""
                    texts.save(update_fields=["normalized_path", "normalized_text", "updated_at"])
                    pipeline_state.normalized_at = timezone.now()
                    pipeline_state.current_stage = PipelineStage.NORMALIZED
                    pipeline_state.save(update_fields=["normalized_at", "current_stage"])

                    item.status = "DONE"
                    item.finished_at = timezone.now()
                    item.overwrote = bool(had_existing)
                    item.save(update_fields=["status", "finished_at", "overwrote"])

                elif run.action == "CHUNK":
                    chunk_lang = "en"
                    edition = Edition.objects.select_related("work", "language").filter(
                        work__code=book_code,
                        language__code="en",
                    ).first()
                    if not edition:
                        raise FileNotFoundError("Edition not found for chunk.")

                    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
                    norm_path, _report, norm_ok = _normalized_status(book_code, chunk_lang)
                    if not norm_ok:
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write("AUTO: normalize before chunk (precheck)\n")
                            norm_path = _run_normalize_dependency(
                                book_code=book_code,
                                lang=chunk_lang,
                                edition=edition,
                                pipeline_state=pipeline_state,
                                log_file=log_file,
                            )
                        norm_path, _report, norm_ok = _normalized_status(book_code, chunk_lang)

                    if not norm_ok:
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("FAIL: precondition missing normalized\n")
                            expected = _normalized_path(book_code, chunk_lang)
                            log_file.write(f"EXPECTED_NORMALIZED_PATH: {expected}\n")
                            log_file.write("CHUNK_SHARED_LANG: en\n")
                            log_file.write("NOTE: Chunking is shared; forced to EN\n")
                        item.status = "FAILED"
                        item.skipped_reason = "PRECONDITION_MISSING_NORMALIZED"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "finished_at", "skipped_reason"])
                        continue

                    chunk_dir = _chunks_dir(book_code, chunk_lang)
                    target_tokens, max_tokens = _chunk_token_limits()
                    command_line = (
                        "python -m gaiden.chunk_book "
                        f"--book {book_code} --lang {chunk_lang} --normalized {norm_path} "
                        f"--out {chunk_dir} --target-tokens {target_tokens} --max-tokens {max_tokens}"
                    )

                    with log_path.open("a", encoding="utf-8") as log_file:
                        chunk_dir, ran_chunk = _run_chunk_dependency(
                            book_code=book_code,
                            norm_path=norm_path,
                            log_file=log_file,
                            skip_existing=skip_existing,
                        )

                    if not ran_chunk and skip_existing:
                        item.status = "SKIPPED"
                        item.skipped_reason = "CHUNKS_EXIST"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    if ran_chunk:
                        pipeline_state.chunked_at = timezone.now()
                        pipeline_state.current_stage = PipelineStage.CHUNKED
                        pipeline_state.save(update_fields=["chunked_at", "current_stage"])

                    item.status = "DONE"
                    item.finished_at = timezone.now()
                    item.overwrote = bool(ran_chunk)
                    item.save(update_fields=["status", "finished_at", "overwrote"])

                elif run.action == "TRANSLATE":
                    edition = Edition.objects.select_related("work", "language").filter(
                        work__code=book_code,
                        language__code="en",
                    ).first()
                    if not edition:
                        raise FileNotFoundError("Edition not found for translate.")

                    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)

                    if not dry_run:
                        source_lang = "en"
                        norm_path, _report, norm_ok = _normalized_status(book_code, source_lang)
                        if not norm_ok:
                            with log_path.open("a", encoding="utf-8") as log_file:
                                log_file.write("AUTO: normalize before translate\n")
                                norm_path = _run_normalize_dependency(
                                    book_code=book_code,
                                    lang=source_lang,
                                    edition=edition,
                                    pipeline_state=pipeline_state,
                                    log_file=log_file,
                                )

                        chunk_dir = _chunks_dir(book_code, source_lang)
                        chunks_ok, _manifest = _chunks_v2_status(chunk_dir)
                        if not chunks_ok:
                            with log_path.open("a", encoding="utf-8") as log_file:
                                log_file.write("AUTO: chunk before translate\n")
                                chunk_dir, ran_chunk = _run_chunk_dependency(
                                    book_code=book_code,
                                    norm_path=norm_path,
                                    log_file=log_file,
                                    skip_existing=False,
                                )
                            if ran_chunk:
                                pipeline_state.chunked_at = timezone.now()
                                pipeline_state.current_stage = PipelineStage.CHUNKED
                                pipeline_state.save(update_fields=["chunked_at", "current_stage"])

                    if skip_existing and merge_path.exists():
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: output exists\n")
                            log_file.write("CHUNKS_LANG_USED: en\n")
                            log_file.write(f"CHUNKS_MANIFEST_PATH: {_chunks_manifest_path(chunk_dir)}\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = "INVALID_STATE"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    if not chunk_dir.exists():
                        raise FileNotFoundError(f"Chunks not found: {chunk_dir}")

                    contract_path = _resolve_contract_path(item.lang)
                    had_existing = merge_path.exists()

                    with log_path.open("a", encoding="utf-8") as log_file:
                        log_file.write(f"COMMAND: {command_line}\n")
                        log_file.write("CHUNKS_LANG_USED: en\n")
                        log_file.write(f"CHUNKS_MANIFEST_PATH: {_chunks_manifest_path(chunk_dir)}\n")
                        if had_existing:
                            log_file.write("NOTE: overwrite existing output\n")
                        log_file.flush()
                        with redirect_stdout(log_file), redirect_stderr(log_file):
                            result = run_translate_safe(
                                book_id=book_code,
                                chunk_dir=str(chunk_dir),
                                out_dir=str(out_dir),
                                suffix=_lang_dir(item.lang),
                                contract_path=contract_path,
                                dry_run=dry_run,
                            )
                            status = result.get("status")
                            if status not in {"ok_official", "ok_fallback"}:
                                raise RuntimeError(f"TRANSLATE_SAFE_FAILED: {status}")

                    if not merge_path.exists():
                        raise FileNotFoundError(f"Merge not found: {merge_path}")

                    item.status = "DONE"
                    item.finished_at = timezone.now()
                    item.overwrote = bool(had_existing)
                    item.save(update_fields=["status", "finished_at", "overwrote"])

                elif run.action == "TRANSLATE_DEFAULT":
                    edition = Edition.objects.select_related("work", "language").filter(
                        work__code=book_code,
                        language__code="en",
                    ).first()
                    if not edition:
                        raise FileNotFoundError("Edition not found for translate.")

                    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)

                    if not dry_run:
                        source_lang = "en"
                        norm_path, _report, norm_ok = _normalized_status(book_code, source_lang)
                        if not norm_ok:
                            with log_path.open("a", encoding="utf-8") as log_file:
                                log_file.write("AUTO: normalize before translate_default\n")
                                norm_path = _run_normalize_dependency(
                                    book_code=book_code,
                                    lang=source_lang,
                                    edition=edition,
                                    pipeline_state=pipeline_state,
                                    log_file=log_file,
                                )

                        chunk_dir = _chunks_dir(book_code, source_lang)
                        chunks_ok, _manifest = _chunks_v2_status(chunk_dir)
                        if not chunks_ok:
                            with log_path.open("a", encoding="utf-8") as log_file:
                                log_file.write("AUTO: chunk before translate_default\n")
                                chunk_dir, ran_chunk = _run_chunk_dependency(
                                    book_code=book_code,
                                    norm_path=norm_path,
                                    log_file=log_file,
                                    skip_existing=False,
                                )
                            if ran_chunk:
                                pipeline_state.chunked_at = timezone.now()
                                pipeline_state.current_stage = PipelineStage.CHUNKED
                                pipeline_state.save(update_fields=["chunked_at", "current_stage"])

                    if skip_existing and merge_path.exists():
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: output exists\n")
                            log_file.write("CHUNKS_LANG_USED: en\n")
                            log_file.write(f"CHUNKS_MANIFEST_PATH: {_chunks_manifest_path(chunk_dir)}\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = "INVALID_STATE"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    if not chunk_dir.exists():
                        raise FileNotFoundError(f"Chunks not found: {chunk_dir}")

                    had_existing = merge_path.exists()

                    with log_path.open("a", encoding="utf-8") as log_file:
                        log_file.write(f"COMMAND: {command_line}\n")
                        log_file.write("CHUNKS_LANG_USED: en\n")
                        log_file.write(f"CHUNKS_MANIFEST_PATH: {_chunks_manifest_path(chunk_dir)}\n")
                        if had_existing:
                            log_file.write("NOTE: overwrite existing output\n")
                        log_file.flush()
                        with redirect_stdout(log_file), redirect_stderr(log_file):
                            run_agent_translate(
                                book_id=book_code,
                                chunk_dir=str(chunk_dir),
                                out_dir=str(out_dir),
                                suffix=_lang_dir(item.lang),
                            )

                    if not merge_path.exists():
                        raise FileNotFoundError(f"Merge not found: {merge_path}")

                    item.status = "DONE"
                    item.finished_at = timezone.now()
                    item.overwrote = bool(had_existing)
                    item.save(update_fields=["status", "finished_at", "overwrote"])

                elif run.action == "SPLIT_FOR_REFINE":
                    if not merge_path.exists():
                        with log_path.open("w", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: missing merge_refine_clean\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = "INVALID_STATE"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    if skip_existing and split_dir.exists():
                        with log_path.open("w", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: output exists\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = "INVALID_STATE"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    had_existing = split_dir.exists()
                    with log_path.open("w", encoding="utf-8") as log_file:
                        log_file.write(f"COMMAND: {command_line}\n")
                        if had_existing:
                            log_file.write("NOTE: overwrite existing output\n")
                        log_file.flush()
                        with redirect_stdout(log_file), redirect_stderr(log_file):
                            process_language(f"book_{book_id:04d}", _lang_dir(item.lang), 2)

                    if not split_dir.exists():
                        raise FileNotFoundError(f"Split dir not found: {split_dir}")

                    item.status = "DONE"
                    item.finished_at = timezone.now()
                    item.overwrote = bool(had_existing)
                    item.save(update_fields=["status", "finished_at", "overwrote"])

            except Exception as exc:
                any_fail = True
                item.status = "FAILED"
                item.finished_at = timezone.now()
                try:
                    with log_path.open("a", encoding="utf-8") as log_file:
                        log_file.write(f"\nERROR: {type(exc).__name__}: {exc}\n")
                        log_file.write(f"COMMAND: {command_line}\n")
                except Exception:
                    pass
                item.save(update_fields=["status", "finished_at"])
                if stop_on_error:
                    break

        if stop_on_error and any_fail:
            remaining = run.items.filter(status="PENDING")
            for item in remaining:
                item.status = "SKIPPED"
                item.finished_at = timezone.now()
                item.skipped_reason = "INVALID_STATE"
                item.save(update_fields=["status", "finished_at", "skipped_reason"])

        run.finished_at = timezone.now()
        run.status = "FAILED" if any_fail else "DONE"
        run.save(update_fields=["status", "finished_at"])

        self.stdout.write(self.style.SUCCESS(f"Run {run.id} finalizado: {run.status}"))
