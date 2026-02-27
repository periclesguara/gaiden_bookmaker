from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import json
from pathlib import Path
import os
import subprocess
import sys

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from gaiden.contracts_v2.resolver import resolve_translate_contract_path
from gaiden.db_preflight import require_active_db
from gaiden.lang import normalize_lang_code
from gaiden.raw_resolver import canonical_raw_dir, resolve_raw_source
from gaiden.translate_engine_v1 import run_translate_safe
from gaiden.translate_mode_policy import apply_skip_policy
from gaiden.tools.agent_translate_default import run_agent_translate
from gaiden.translate_artifacts import (
    active_pointer_filename,
    normalize_book_code,
    resolve_active_or_latest,
)
from gaiden.refine_split import process_language
from editorial.models import EditionPipeline, EditionText, PipelineStage, Edition
from pipeline.models import PipelineRun, PipelineRunItem
from pipeline.services.export_book import run_export_epub
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
    pointer_path = out_dir / active_pointer_filename(book_code, lang)
    return chunk_dir, out_dir, pointer_path


def _split_paths(book_id: int, lang: str) -> tuple[Path, Path]:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    lang_dir = _lang_dir(lang)
    base_dir = data_dir / "translated" / f"book_{book_id:04d}" / lang_dir
    split_dir = base_dir / "split_chapters_for_refine"
    return base_dir, split_dir


def _build_output_path(book_code: str, lang: str) -> Path:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    lang_code = _lang_db_code(lang)
    return data_dir / "builds" / book_code / lang_code / f"{book_code}_{lang_code}_book.md"


def _epub_output_path(book_code: str, lang: str) -> Path:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    lang_code = _lang_db_code(lang)
    return data_dir / "builds" / book_code / lang_code / "BOOK.epub"


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


def _fixed_text_path(book_code: str, lang: str) -> Path:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    lang_code = _lang_fs(lang)
    return data_dir / "normalized" / book_code / lang_code / "normalized.fixed.md"


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


def _is_truthy(value: object) -> bool:
    raw = str(value or "").strip().lower()
    return raw in {"1", "true", "yes", "on", "y"}


def _active_merge_target(pointer_path: Path | None, fallback_name: str | None = None) -> str | None:
    if pointer_path and pointer_path.exists():
        try:
            target = pointer_path.read_text(encoding="utf-8").strip()
        except OSError:
            target = ""
        if target:
            return target
    return fallback_name


def _compact_status(raw_status: str | None) -> str:
    status = str(raw_status or "").strip().lower()
    if status in {"ok", "ok_official", "ok_fallback", "ok_default"}:
        return "ok"
    if status in {"skipped", "skip"}:
        return "skipped"
    if status in {"error_preflight"}:
        return "error_preflight"
    if status:
        return status
    return "unknown"


def _compact_errors_summary(result: dict | None, *, fallback_error: str | None = None) -> list[str] | str:
    if isinstance(result, dict):
        errors = result.get("errors")
        if isinstance(errors, list):
            cleaned = [str(e).strip() for e in errors if str(e).strip()]
            if cleaned:
                return cleaned[:5]
        if isinstance(errors, str) and errors.strip():
            return errors.strip()
        status = str(result.get("status") or "").strip()
        if status and status not in {"ok", "ok_official", "ok_fallback", "ok_default"}:
            return status
    if fallback_error:
        return fallback_error
    return []


def _fallback_reason(result: dict | None) -> str:
    if not isinstance(result, dict):
        return "none"
    fallback_used = bool(result.get("fallback_used", False))
    if not fallback_used:
        return "none"
    explicit = str(result.get("fallback_reason") or "").strip().lower()
    if explicit in {"policy", "content_filter"}:
        return explicit
    errors = result.get("errors")
    joined = ""
    if isinstance(errors, list):
        joined = " ".join(str(e) for e in errors).lower()
    elif isinstance(errors, str):
        joined = errors.lower()
    if "content_filter" in joined or "content filtered" in joined:
        return "content_filter"
    if "policy" in joined:
        return "policy"
    return "policy"


def _append_runs_summary(core: dict) -> None:
    if not _is_truthy(os.getenv("GAIDEN_REPORT_V2_NDJSON", "0")):
        return
    logs_dir = Path(settings.BASE_DIR).parent / "data" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    summary_path = logs_dir / "runs_summary.ndjson"
    with summary_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(core, ensure_ascii=False, separators=(",", ":")) + "\n")


def _emit_report_v2(
    *,
    log_path: Path,
    run_id: int,
    book: str,
    lang: str,
    selected_mode: str,
    effective_mode: str,
    fallback_used: bool,
    fallback_reason: str,
    split_mode: str,
    refine_mode: str,
    preflight_ok: bool,
    status: str,
    exit_code: int,
    artifact_filename: str | None,
    active_merge_target: str | None,
    artifact_sha256: str | None,
    errors_summary: list[str] | str,
    debug_enabled: bool,
    skip_policy: dict,
    duration_ms_total: int | None = None,
    duration_ms_translate: int | None = None,
    chunks_total: int | None = None,
    artifact_path_full: str | None = None,
    active_pointer_path: str | None = None,
) -> None:
    core = {
        "run_id": str(run_id),
        "book": book,
        "lang": lang,
        "ts": timezone.now().isoformat(),
        "selected_mode": selected_mode,
        "effective_mode": effective_mode,
        "fallback_used": bool(fallback_used),
        "fallback_reason": fallback_reason,
        "split_mode": split_mode,
        "refine_mode": refine_mode,
        "preflight_ok": bool(preflight_ok),
        "status": _compact_status(status),
        "exit_code": int(exit_code),
        "artifact_filename": artifact_filename,
        "active_merge_target": active_merge_target,
        "artifact_sha256": artifact_sha256,
        "errors_summary": errors_summary,
    }

    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(
            "REPORT_V2 "
            + json.dumps(core, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )
        if debug_enabled:
            debug_payload: dict[str, object] = {
                "skip_requested": bool(skip_policy.get("skip_requested")),
                "skip_applied": bool(skip_policy.get("skip_applied")),
                "skip_block_reason": skip_policy.get("skip_block_reason"),
                "skip_corrected": bool(skip_policy.get("skip_corrected")),
                "skip_original_split_mode": skip_policy.get("skip_original_split_mode"),
                "skip_original_refine_mode": skip_policy.get("skip_original_refine_mode"),
            }
            if duration_ms_total is not None:
                debug_payload["duration_ms_total"] = duration_ms_total
            if duration_ms_translate is not None:
                debug_payload["duration_ms_translate"] = duration_ms_translate
            if chunks_total is not None:
                debug_payload["chunks_total"] = chunks_total
            if artifact_path_full is not None:
                debug_payload["artifact_path_full"] = artifact_path_full
            if active_pointer_path is not None:
                debug_payload["active_pointer_path"] = active_pointer_path
            log_file.write(
                "REPORT_V2_DEBUG "
                + json.dumps(debug_payload, ensure_ascii=False, separators=(",", ":"))
                + "\n"
            )
    try:
        _append_runs_summary(core)
    except Exception:
        # Logging side-channel must never break pipeline execution.
        pass


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
    help = "Run MATRIX pipeline queue (normalize/chunk/translate/split/build/export)."

    def add_arguments(self, parser):
        parser.add_argument("run_id", nargs="?", type=int, help="PipelineRun id")
        parser.add_argument("--book", type=str, default=None, help="book code (book_0003)")
        parser.add_argument("--lang", type=str, default=None, help="language (en, es, ptbr, ...)")
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Emit REPORT_V2_DEBUG blocks for per-item logs.",
        )
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
        debug_enabled = bool(options.get("debug")) or _is_truthy(os.getenv("GAIDEN_DEBUG", "0"))

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
        require_active_db()
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
        selected_mode = (
            "default"
            if run.action == "TRANSLATE_DEFAULT"
            else opts.get("selected_mode", opts.get("translate_mode"))
        )
        policy = apply_skip_policy(
            selected_mode=selected_mode,
            split_mode=opts.get("split_mode"),
            refine_mode=opts.get("refine_mode"),
        )
        translate_mode = policy["selected_mode"]
        split_mode = policy["split_mode"]
        refine_mode = policy["refine_mode"]

        options_changed = False
        merged_opts = dict(opts)
        for key, value in {
            "translate_mode": translate_mode,
            "selected_mode": policy["selected_mode"],
            "effective_mode": policy["effective_mode"],
            "split_mode": split_mode,
            "refine_mode": refine_mode,
            "skip_requested": policy["skip_requested"],
            "skip_applied": policy["skip_applied"],
            "skip_block_reason": policy["skip_block_reason"],
            "skip_corrected": policy["skip_corrected"],
            "skip_original_split_mode": policy.get("skip_original_split_mode"),
            "skip_original_refine_mode": policy.get("skip_original_refine_mode"),
        }.items():
            if merged_opts.get(key) != value:
                merged_opts[key] = value
                options_changed = True
        if options_changed:
            run.options = merged_opts
            run.save(update_fields=["options"])

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

            pointer_path: Path | None = None
            result: dict | None = None
            translate_stage_started = None
            translate_stage_finished = None

            try:
                command_line = "n/a"
                book_code = item.book_code or f"book_{book_id:04d}"
                translate_mode_for_item = translate_mode
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
                    normalized_path = _fixed_text_path(book_code, chunk_lang)
                    target_tokens, max_tokens = _chunk_token_limits()
                    command_line = (
                        "python -m gaiden.chunk_book "
                        f"--book {book_code} --lang {chunk_lang} "
                        f"--normalized {normalized_path or 'MISSING'} "
                        f"--out {chunk_dir} --target-tokens {target_tokens} --max-tokens {max_tokens}"
                    )
                elif run.action == "TRANSLATE":
                    chunk_dir, out_dir, pointer_path = _translate_paths(book_id, book_code, item.lang)
                    item.out_path = str(pointer_path)
                    item.save(update_fields=["out_path"])
                    if translate_mode_for_item == "default":
                        command_line = (
                            "run_agent_translate("
                            f"book_id={book_code}, chunk_dir={chunk_dir}, out_dir={out_dir}, "
                            f"suffix={_lang_dir(item.lang)}, mode=default, agent=ALAMAGUEDERAZ)"
                        )
                    else:
                        command_line = (
                            "run_translate_safe("
                            f"book_id={book_code}, chunk_dir={chunk_dir}, out_dir={out_dir}, "
                            f"suffix={_lang_dir(item.lang)}, contract={_resolve_contract_path(item.lang)}, "
                            "selected_mode=automatic)"
                        )
                elif run.action == "TRANSLATE_DEFAULT":
                    chunk_dir, out_dir, pointer_path = _translate_paths(book_id, book_code, item.lang)
                    item.out_path = str(pointer_path)
                    item.save(update_fields=["out_path"])
                    translate_mode_for_item = "default"
                    command_line = (
                        "run_agent_translate("
                        f"book_id={book_code}, chunk_dir={chunk_dir}, out_dir={out_dir}, "
                        f"suffix={_lang_dir(item.lang)}, mode=default, agent=ALAMAGUEDERAZ)"
                    )
                elif run.action == "SPLIT_FOR_REFINE":
                    base_dir, split_dir = _split_paths(book_id, item.lang)
                    item.out_path = str(split_dir)
                    item.save(update_fields=["out_path"])
                    command_line = (
                        "process_language("
                        f"book='book_{book_id:04d}', lang='{_lang_dir(item.lang)}', parts=2)"
                    )
                elif run.action == "BUILD":
                    build_path = _build_output_path(book_code, item.lang)
                    item.out_path = str(build_path)
                    item.save(update_fields=["out_path"])
                    command_line = (
                        "manage.py build_book_text "
                        f"--book-code={book_code} --language={_lang_db_code(item.lang)}"
                    )
                elif run.action == "EXPORT_EPUB":
                    epub_path = _epub_output_path(book_code, item.lang)
                    item.out_path = str(epub_path)
                    item.save(update_fields=["out_path"])
                    command_line = (
                        "pipeline.services.export_book.run_export_epub("
                        f"edition={book_code}[{_lang_db_code(item.lang)}])"
                    )
                else:
                    raise ValueError(f"Unsupported action: {run.action}")

                precheck = None
                if run.action in {"NORMALIZE", "CHUNK", "TRANSLATE", "TRANSLATE_DEFAULT"}:
                    precheck = _run_precheck(
                        book_code=book_code,
                        lang=item.lang if run.action == "NORMALIZE" else "en",
                        log_path=log_path,
                        ensure_normalized=(run.action in {"NORMALIZE", "TRANSLATE", "TRANSLATE_DEFAULT"}),
                        ensure_chunks=(run.action in {"TRANSLATE", "TRANSLATE_DEFAULT"}),
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
                        if run.action in {"TRANSLATE", "TRANSLATE_DEFAULT"}:
                            duration_total = None
                            if item.started_at and item.finished_at:
                                duration_total = int(
                                    (item.finished_at - item.started_at).total_seconds() * 1000
                                )
                            _emit_report_v2(
                                log_path=log_path,
                                run_id=run.id,
                                book=book_code,
                                lang=_lang_dir(item.lang),
                                selected_mode=translate_mode_for_item,
                                effective_mode=translate_mode_for_item,
                                fallback_used=False,
                                fallback_reason="none",
                                split_mode=split_mode,
                                refine_mode=refine_mode,
                                preflight_ok=False,
                                status="skipped",
                                exit_code=2,
                                artifact_filename=None,
                                active_merge_target=_active_merge_target(pointer_path, None),
                                artifact_sha256=None,
                                errors_summary=[precheck["raw_reason"]],
                                debug_enabled=debug_enabled,
                                skip_policy=policy,
                                duration_ms_total=duration_total,
                                active_pointer_path=str(pointer_path) if pointer_path else None,
                            )
                        continue

                if dry_run:
                    with log_path.open("a", encoding="utf-8") as log_file:
                        log_file.write(f"COMMAND: {command_line}\n")
                        log_file.write("DRY-RUN: no execution\n")
                    item.status = "SKIPPED"
                    item.skipped_reason = "INVALID_STATE"
                    item.finished_at = timezone.now()
                    item.save(update_fields=["status", "skipped_reason", "finished_at"])
                    if run.action in {"TRANSLATE", "TRANSLATE_DEFAULT"}:
                        duration_total = None
                        if item.started_at and item.finished_at:
                            duration_total = int(
                                (item.finished_at - item.started_at).total_seconds() * 1000
                            )
                        _emit_report_v2(
                            log_path=log_path,
                            run_id=run.id,
                            book=book_code,
                            lang=_lang_dir(item.lang),
                            selected_mode=translate_mode_for_item,
                            effective_mode=translate_mode_for_item,
                            fallback_used=False,
                            fallback_reason="none",
                            split_mode=split_mode,
                            refine_mode=refine_mode,
                            preflight_ok=True,
                            status="dry_run",
                            exit_code=0,
                            artifact_filename=None,
                            active_merge_target=_active_merge_target(pointer_path, None),
                            artifact_sha256=None,
                            errors_summary=[],
                            debug_enabled=debug_enabled,
                            skip_policy=policy,
                            duration_ms_total=duration_total,
                            active_pointer_path=str(pointer_path) if pointer_path else None,
                        )
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
                    edition_status = str(edition.status or "").strip().upper()
                    if edition_status != "FIXED_TEXT":
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write("FAIL: CHUNK gate requires Edition.status=FIXED_TEXT\n")
                            log_file.write(f"CURRENT_STATUS: {edition_status or 'MISSING'}\n")
                        item.status = "FAILED"
                        item.skipped_reason = "PRECONDITION_STATUS_NOT_FIXED_TEXT"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "finished_at", "skipped_reason"])
                        continue

                    norm_path = _fixed_text_path(book_code, chunk_lang)
                    if not norm_path.exists():
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("FAIL: precondition missing fixed normalized text\n")
                            log_file.write(f"EXPECTED_FIXED_PATH: {norm_path}\n")
                            log_file.write("CHUNK_SHARED_LANG: en\n")
                            log_file.write("NOTE: Chunking is shared; forced to EN\n")
                        item.status = "FAILED"
                        item.skipped_reason = "PRECONDITION_MISSING_FIXED_TEXT"
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

                elif run.action in {"TRANSLATE", "TRANSLATE_DEFAULT"}:
                    if run.action == "TRANSLATE_DEFAULT":
                        translate_mode_for_item = "default"
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

                    existing_merge = resolve_active_or_latest(out_dir, book_code, _lang_dir(item.lang))
                    if skip_existing and existing_merge and existing_merge.exists():
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: output exists\n")
                            log_file.write(f"ACTIVE_MERGE: {existing_merge}\n")
                            log_file.write("CHUNKS_LANG_USED: en\n")
                            log_file.write(f"CHUNKS_MANIFEST_PATH: {_chunks_manifest_path(chunk_dir)}\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = "INVALID_STATE"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        duration_total = None
                        if item.started_at and item.finished_at:
                            duration_total = int(
                                (item.finished_at - item.started_at).total_seconds() * 1000
                            )
                        _emit_report_v2(
                            log_path=log_path,
                            run_id=run.id,
                            book=book_code,
                            lang=_lang_dir(item.lang),
                            selected_mode=translate_mode_for_item,
                            effective_mode=translate_mode_for_item,
                            fallback_used=False,
                            fallback_reason="none",
                            split_mode=split_mode,
                            refine_mode=refine_mode,
                            preflight_ok=True,
                            status="skipped",
                            exit_code=0,
                            artifact_filename=existing_merge.name,
                            active_merge_target=_active_merge_target(pointer_path, existing_merge.name),
                            artifact_sha256=None,
                            errors_summary=[],
                            debug_enabled=debug_enabled,
                            skip_policy=policy,
                            duration_ms_total=duration_total,
                            artifact_path_full=str(existing_merge),
                            active_pointer_path=str(pointer_path) if pointer_path else None,
                        )
                        continue

                    if not chunk_dir.exists():
                        raise FileNotFoundError(f"Chunks not found: {chunk_dir}")

                    had_existing = bool(existing_merge and existing_merge.exists())

                    with log_path.open("a", encoding="utf-8") as log_file:
                        log_file.write(f"COMMAND: {command_line}\n")
                        log_file.write(f"TRANSLATE_MODE_SELECTED: {translate_mode_for_item}\n")
                        log_file.write(
                            f"EFFECTIVE_MODE: {policy['effective_mode']}\n"
                        )
                        log_file.write(f"SPLIT_MODE: {split_mode}\n")
                        log_file.write(f"REFINE_MODE: {refine_mode}\n")
                        log_file.write(f"SKIP_REQUESTED: {policy['skip_requested']}\n")
                        log_file.write(f"SKIP_APPLIED: {policy['skip_applied']}\n")
                        if policy["skip_block_reason"]:
                            log_file.write(
                                f"SKIP_BLOCK_REASON: {policy['skip_block_reason']}\n"
                            )
                        if policy["skip_corrected"]:
                            log_file.write("SKIP_CORRECTED: true\n")
                            log_file.write(
                                f"SKIP_ORIGINAL_SPLIT_MODE: {policy.get('skip_original_split_mode')}\n"
                            )
                            log_file.write(
                                f"SKIP_ORIGINAL_REFINE_MODE: {policy.get('skip_original_refine_mode')}\n"
                            )
                        else:
                            log_file.write("SKIP_CORRECTED: false\n")
                        log_file.write("CHUNKS_LANG_USED: en\n")
                        log_file.write(f"CHUNKS_MANIFEST_PATH: {_chunks_manifest_path(chunk_dir)}\n")
                        if had_existing:
                            log_file.write("NOTE: overwrite existing output\n")
                        log_file.flush()
                        translate_stage_started = timezone.now()
                        with redirect_stdout(log_file), redirect_stderr(log_file):
                            if translate_mode_for_item == "default":
                                default_run = run_agent_translate(
                                    book_id=book_code,
                                    chunk_dir=str(chunk_dir),
                                    out_dir=str(out_dir),
                                    suffix=_lang_dir(item.lang),
                                    mode="default",
                                )
                                if default_run.get("status") != "ok":
                                    raise RuntimeError(
                                        f"TRANSLATE_DEFAULT_FAILED: {default_run.get('status')}"
                                    )
                                status = "ok_default"
                                result = {
                                    "status": status,
                                    "merged_txt": default_run.get("merged_txt"),
                                    "selected_mode": "default",
                                    "final_mode": "default",
                                    "effective_route": "default",
                                    "fallback_used": False,
                                    "fallback_reason": "none",
                                    "preflight_ok": bool(default_run.get("preflight_ok", True)),
                                    "exit_code": int(default_run.get("exit_code") or 0),
                                    "artifact_filename": default_run.get("artifact_filename"),
                                    "artifact_sha256": default_run.get("artifact_sha256"),
                                    "errors": default_run.get("errors", []),
                                    "merged_count": default_run.get("merged_count"),
                                }
                            else:
                                contract_path = _resolve_contract_path(item.lang)
                                result = run_translate_safe(
                                    book_id=book_code,
                                    chunk_dir=str(chunk_dir),
                                    out_dir=str(out_dir),
                                    suffix=_lang_dir(item.lang),
                                    contract_path=contract_path,
                                    dry_run=dry_run,
                                    selected_mode="automatic",
                                )
                                status = result.get("status")
                                if status not in {"ok_official", "ok_fallback"}:
                                    raise RuntimeError(f"TRANSLATE_SAFE_FAILED: {status}")
                        translate_stage_finished = timezone.now()

                    merged_path = None
                    merged_txt = result.get("merged_txt")
                    if merged_txt:
                        merged_path = Path(str(merged_txt))
                    if not merged_path or not merged_path.exists():
                        merged_path = resolve_active_or_latest(out_dir, book_code, _lang_dir(item.lang))
                    if not merged_path or not merged_path.exists():
                        raise FileNotFoundError("Canonical merge artifact not found after translate.")
                    item.out_path = str(merged_path)
                    item.save(update_fields=["out_path"])

                    with log_path.open("a", encoding="utf-8") as log_file:
                        log_file.write(f"ARTIFACT: {merged_path}\n")
                        log_file.write(f"TRANSLATE_STATUS: {result.get('status')}\n")
                        log_file.write(
                            f"SELECTED_MODE: {result.get('selected_mode', translate_mode_for_item)}\n"
                        )
                        log_file.write(f"FINAL_MODE: {result.get('final_mode', translate_mode_for_item)}\n")
                        log_file.write(
                            f"EFFECTIVE_ROUTE: {result.get('effective_route', result.get('final_mode', translate_mode_for_item))}\n"
                        )
                        log_file.write(
                            f"FALLBACK_USED: {bool(result.get('fallback_used', False))}\n"
                        )

                    _base_dir, split_dir = _split_paths(book_id, item.lang)
                    if split_mode == "do":
                        if skip_existing and split_dir.exists():
                            with log_path.open("a", encoding="utf-8") as log_file:
                                log_file.write("SKIP_SPLIT: output exists\n")
                        else:
                            split_had_existing = split_dir.exists()
                            with log_path.open("a", encoding="utf-8") as log_file:
                                if split_had_existing:
                                    log_file.write("NOTE: split output overwritten\n")
                                with redirect_stdout(log_file), redirect_stderr(log_file):
                                    created = process_language(book_code, _lang_dir(item.lang), 2)
                            if created <= 0 or not split_dir.exists():
                                raise RuntimeError("SPLIT_FOR_REFINE_FAILED: no split files generated")
                    else:
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write("SKIP_SPLIT: split_mode=skip\n")

                    if refine_mode == "do":
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write("refine_scheduled=true\n")
                            log_file.write("refine_executed=false\n")
                            log_file.write(
                                "REFINE_MODE=do (deferred): execute RETURN_REFINE stage in dedicated run.\n"
                            )
                    else:
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write("refine_scheduled=false\n")
                            log_file.write("refine_executed=false\n")
                            log_file.write("SKIP_REFINE: refine_mode=skip\n")

                    effective_mode = str(
                        result.get(
                            "effective_route",
                            result.get("final_mode", translate_mode_for_item),
                        )
                    )
                    selected_mode_result = str(
                        result.get("selected_mode", translate_mode_for_item)
                    )
                    fallback_used = bool(result.get("fallback_used", False))
                    artifact_filename = str(
                        result.get("artifact_filename") or merged_path.name
                    )
                    artifact_sha256 = result.get("artifact_sha256")
                    exit_code = int(result.get("exit_code") or 0)
                    preflight_ok = bool(result.get("preflight_ok", True))
                    status = str(result.get("status") or "ok")
                    errors_summary = _compact_errors_summary(result)
                    fallback_reason = _fallback_reason(result)

                    item.status = "DONE"
                    item.finished_at = timezone.now()
                    item.overwrote = bool(had_existing)
                    item.save(update_fields=["status", "finished_at", "overwrote"])
                    duration_total = None
                    if item.started_at and item.finished_at:
                        duration_total = int(
                            (item.finished_at - item.started_at).total_seconds() * 1000
                        )
                    duration_translate = None
                    if translate_stage_started and translate_stage_finished:
                        duration_translate = int(
                            (translate_stage_finished - translate_stage_started).total_seconds()
                            * 1000
                        )
                    _emit_report_v2(
                        log_path=log_path,
                        run_id=run.id,
                        book=book_code,
                        lang=_lang_dir(item.lang),
                        selected_mode=selected_mode_result,
                        effective_mode=effective_mode,
                        fallback_used=fallback_used,
                        fallback_reason=fallback_reason,
                        split_mode=split_mode,
                        refine_mode=refine_mode,
                        preflight_ok=preflight_ok,
                        status=status,
                        exit_code=exit_code,
                        artifact_filename=artifact_filename,
                        active_merge_target=_active_merge_target(pointer_path, artifact_filename),
                        artifact_sha256=str(artifact_sha256) if artifact_sha256 else None,
                        errors_summary=errors_summary,
                        debug_enabled=debug_enabled,
                        skip_policy=policy,
                        duration_ms_total=duration_total,
                        duration_ms_translate=duration_translate,
                        chunks_total=(
                            int(result["merged_count"])
                            if isinstance(result.get("merged_count"), int)
                            else None
                        ),
                        artifact_path_full=str(merged_path),
                        active_pointer_path=str(pointer_path) if pointer_path else None,
                    )

                elif run.action == "SPLIT_FOR_REFINE":
                    canonical_merge = resolve_active_or_latest(
                        base_dir,
                        normalize_book_code(book_code),
                        _lang_dir(item.lang),
                    )
                    if not canonical_merge or not canonical_merge.exists():
                        with log_path.open("w", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: missing canonical merge artifact\n")
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

                elif run.action == "BUILD":
                    edition = Edition.objects.select_related("work", "language").filter(
                        work__code=book_code,
                        language__code=_lang_db_code(item.lang),
                    ).first()
                    if not edition:
                        raise FileNotFoundError("Edition not found for build.")

                    out_path = _build_output_path(book_code, item.lang)
                    had_existing = out_path.exists()
                    if skip_existing and had_existing:
                        with log_path.open("w", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: output exists\n")
                            log_file.write(f"OUTPUT: {out_path}\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = "OUTPUT_EXISTS"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    with log_path.open("w", encoding="utf-8") as log_file:
                        log_file.write(f"COMMAND: {command_line}\n")
                        log_file.write(f"OUTPUT: {out_path}\n")
                        if had_existing:
                            log_file.write("NOTE: overwrite existing output\n")
                        with redirect_stdout(log_file), redirect_stderr(log_file):
                            call_command(
                                "build_book_text",
                                book_code=book_code,
                                language=edition.language.code,
                            )

                    if not out_path.exists():
                        raise FileNotFoundError(f"Build output not found: {out_path}")
                    item.out_path = str(out_path)
                    item.status = "DONE"
                    item.finished_at = timezone.now()
                    item.overwrote = bool(had_existing)
                    item.save(update_fields=["out_path", "status", "finished_at", "overwrote"])

                elif run.action == "EXPORT_EPUB":
                    edition = Edition.objects.select_related("work", "language").filter(
                        work__code=book_code,
                        language__code=_lang_db_code(item.lang),
                    ).first()
                    if not edition:
                        raise FileNotFoundError("Edition not found for export.")

                    out_path = _epub_output_path(book_code, item.lang)
                    had_existing = out_path.exists()
                    if skip_existing and had_existing:
                        with log_path.open("w", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: output exists\n")
                            log_file.write(f"OUTPUT: {out_path}\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = "OUTPUT_EXISTS"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    with log_path.open("w", encoding="utf-8") as log_file:
                        log_file.write(f"COMMAND: {command_line}\n")
                        if had_existing:
                            log_file.write("NOTE: overwrite existing output\n")
                        with redirect_stdout(log_file), redirect_stderr(log_file):
                            export_result = run_export_epub(edition)
                        log_file.write(f"OUTPUT: {export_result.get('path', '')}\n")

                    exported_path = Path(str(export_result.get("path") or out_path))
                    if not exported_path.exists():
                        raise FileNotFoundError(f"EPUB not found: {exported_path}")
                    item.out_path = str(exported_path)
                    item.status = "DONE"
                    item.finished_at = timezone.now()
                    item.overwrote = bool(had_existing)
                    item.save(update_fields=["out_path", "status", "finished_at", "overwrote"])

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
                if run.action in {"TRANSLATE", "TRANSLATE_DEFAULT"}:
                    duration_total = None
                    if item.started_at and item.finished_at:
                        duration_total = int(
                            (item.finished_at - item.started_at).total_seconds() * 1000
                        )
                    duration_translate = None
                    if translate_stage_started and translate_stage_finished:
                        duration_translate = int(
                            (translate_stage_finished - translate_stage_started).total_seconds()
                            * 1000
                        )
                    selected_mode_result = str(
                        (result or {}).get("selected_mode", translate_mode_for_item)
                    )
                    effective_mode = str(
                        (result or {}).get(
                            "effective_route",
                            (result or {}).get("final_mode", translate_mode_for_item),
                        )
                    )
                    artifact_filename = (result or {}).get("artifact_filename")
                    artifact_sha256 = (result or {}).get("artifact_sha256")
                    preflight_ok = bool((result or {}).get("preflight_ok", False))
                    exit_code = int((result or {}).get("exit_code") or 3)
                    errors_summary = _compact_errors_summary(
                        result,
                        fallback_error=f"{type(exc).__name__}:{exc}",
                    )
                    _emit_report_v2(
                        log_path=log_path,
                        run_id=run.id,
                        book=book_code,
                        lang=_lang_dir(item.lang),
                        selected_mode=selected_mode_result,
                        effective_mode=effective_mode,
                        fallback_used=bool((result or {}).get("fallback_used", False)),
                        fallback_reason=_fallback_reason(result),
                        split_mode=split_mode,
                        refine_mode=refine_mode,
                        preflight_ok=preflight_ok,
                        status=(result or {}).get("status", "failed"),
                        exit_code=exit_code,
                        artifact_filename=str(artifact_filename) if artifact_filename else None,
                        active_merge_target=_active_merge_target(
                            pointer_path,
                            str(artifact_filename) if artifact_filename else None,
                        ),
                        artifact_sha256=(
                            str(artifact_sha256) if artifact_sha256 else None
                        ),
                        errors_summary=errors_summary,
                        debug_enabled=debug_enabled,
                        skip_policy=policy,
                        duration_ms_total=duration_total,
                        duration_ms_translate=duration_translate,
                        chunks_total=(
                            int((result or {}).get("merged_count"))
                            if isinstance((result or {}).get("merged_count"), int)
                            else None
                        ),
                        artifact_path_full=str((result or {}).get("merged_txt"))
                        if (result or {}).get("merged_txt")
                        else None,
                        active_pointer_path=str(pointer_path) if pointer_path else None,
                    )
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
