from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import json
from pathlib import Path
import subprocess
import sys

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from gaiden.translate import run_translate_with_contract
from gaiden.split_merge_translate_for_refine import process_language
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
    if utils.normalize_lang(lang) == "ptbr":
        return "PT-BR"
    return utils.normalize_lang(lang).upper()


def _lang_fs(lang: str) -> str:
    return utils.normalize_lang(lang)


def _lang_db_code(lang: str) -> str:
    norm = utils.normalize_lang(lang)
    return "pt-br" if norm == "ptbr" else norm


def _translate_paths(book_id: int, book_code: str, lang: str) -> tuple[Path, Path, Path]:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    chunk_dir = data_dir / "chunks" / book_code / "en"
    out_dir = data_dir / "translated" / f"book_{book_id:04d}" / _lang_dir(lang)
    merge_path = out_dir / f"merge_translate_{_lang_dir(lang)}.txt"
    return chunk_dir, out_dir, merge_path


def _split_paths(book_id: int, lang: str) -> tuple[Path, Path, Path]:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    lang_dir = _lang_dir(lang)
    base_dir = data_dir / "translated" / f"book_{book_id:04d}" / lang_dir
    merge_path = base_dir / f"merge_translate_{lang_dir}.txt"
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


def _raw_lang_upper(lang: str) -> str:
    norm = utils.normalize_lang(lang)
    return "PT-BR" if norm == "ptbr" else norm.upper()


def _raw_dirs(book_code: str, lang: str) -> tuple[Path, Path]:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    lang_lower = _lang_fs(lang)
    base = data_dir / "raw" / book_code
    return base / lang_lower, base / _raw_lang_upper(lang)


def _resolve_raw_path(book_code: str, lang: str) -> tuple[Path | None, Path, str | None]:
    lower_dir, upper_dir = _raw_dirs(book_code, lang)

    def _select_source(dir_path: Path) -> tuple[Path | None, str | None]:
        txt_path = dir_path / "source.txt"
        md_path = dir_path / "source.md"
        txt_exists = txt_path.exists()
        md_exists = md_path.exists()
        if txt_exists and md_exists:
            return None, "INVALID_STATE"
        if txt_exists:
            return txt_path, None
        if md_exists:
            return md_path, None
        return None, "RAW_MISSING"

    lower_source, lower_reason = _select_source(lower_dir)
    if lower_source:
        return lower_source, lower_dir, None
    if lower_reason == "INVALID_STATE":
        return None, lower_dir, "INVALID_STATE"

    upper_source, upper_reason = _select_source(upper_dir)
    if upper_source:
        return upper_source, upper_dir, None
    if upper_reason == "INVALID_STATE":
        return None, upper_dir, "INVALID_STATE"

    return None, lower_dir, "RAW_MISSING"


def _chunks_dir(book_code: str, lang: str) -> Path:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    return data_dir / "chunks" / book_code / _lang_fs(lang)


def _chunks_manifest_path(chunks_dir: Path) -> Path:
    return chunks_dir / "chunks_manifest.json"


def _chunks_exist(chunks_dir: Path) -> bool:
    return _chunks_manifest_path(chunks_dir).exists()


def _remove_existing_chunks(chunks_dir: Path) -> None:
    if not chunks_dir.exists():
        return
    for path in chunks_dir.glob("ch_*_chunk_*.txt"):
        path.unlink()
    manifest = _chunks_manifest_path(chunks_dir)
    if manifest.exists():
        manifest.unlink()


def _resolve_contract_path(lang: str) -> Path:
    mapping = {
        "en": "gaiden/contracts/en_modern_2025.json",
        "es": "gaiden/contracts/en_es_2025.json",
        "ptbr": "gaiden/contracts/en_ptbr_2025.json",
        "de": "gaiden/contracts/en_de_krimi_2025.json",
        "fr": "gaiden/contracts/translate_fr_2026.json",
        "it": "gaiden/contracts/translate_it_2026.json",
    }
    key = utils.normalize_lang(lang)
    rel = mapping.get(key)
    if not rel:
        raise ValueError(f"No translate contract for language={lang}")
    return Path(settings.BASE_DIR).parent / rel


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


class Command(BaseCommand):
    help = "Run MATRIX pipeline queue (sequential, translate-only for MVP)."

    def add_arguments(self, parser):
        parser.add_argument("run_id", type=int, help="PipelineRun id")

    def handle(self, *args, **options):
        run_id = options["run_id"]
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
                    chunk_dir = _chunks_dir(book_code, item.lang)
                    manifest_path = _chunks_manifest_path(chunk_dir)
                    item.out_path = str(manifest_path)
                    item.save(update_fields=["out_path"])
                    normalized_path = _resolve_normalized_path(book_code, item.lang)
                    command_line = (
                        "python -m gaiden.chunk_book "
                        f"--book {book_code} --lang {_lang_fs(item.lang)} "
                        f"--normalized {normalized_path or 'MISSING'} "
                        f"--out {chunk_dir} --target-chars 5600 --max-chars 6000"
                    )
                elif run.action == "TRANSLATE":
                    chunk_dir, out_dir, merge_path = _translate_paths(book_id, book_code, item.lang)
                    item.out_path = str(merge_path)
                    item.save(update_fields=["out_path"])

                    command_line = (
                        "run_translate_with_contract("
                        f"contract={_resolve_contract_path(item.lang)}, "
                        f"chunk_dir={chunk_dir}, out_dir={out_dir})"
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

                if dry_run:
                    with log_path.open("w", encoding="utf-8") as log_file:
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

                    raw_path, raw_dir, raw_reason = _resolve_raw_path(book_code, item.lang)
                    if raw_reason:
                        with log_path.open("w", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write(f"RAW_DIR_SELECTED: {raw_dir}\n")
                            log_file.write(f"SKIP: {raw_reason}\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = raw_reason
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    norm_path = _normalized_path(book_code, item.lang)
                    if skip_existing and norm_path.exists():
                        with log_path.open("w", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: normalized exists\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = "NORMALIZED_EXISTS"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    had_existing = norm_path.exists()
                    with log_path.open("w", encoding="utf-8") as log_file:
                        log_file.write(f"RAW_DIR_SELECTED: {raw_dir}\n")
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
                    edition = Edition.objects.select_related("work", "language").filter(
                        work__code=book_code,
                        language__code=_lang_db_code(item.lang),
                    ).first()
                    if not edition:
                        raise FileNotFoundError("Edition not found for chunk.")

                    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
                    norm_path = _resolve_normalized_path(book_code, item.lang)
                    if not norm_path:
                        with log_path.open("w", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("FAIL: precondition missing normalized\n")
                            expected = _normalized_path(book_code, item.lang)
                            log_file.write(f"EXPECTED_NORMALIZED_PATH: {expected}\n")
                        item.status = "FAILED"
                        item.skipped_reason = "PRECONDITION_MISSING_NORMALIZED"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "finished_at", "skipped_reason"])
                        continue

                    chunk_dir = _chunks_dir(book_code, item.lang)
                    command_line = (
                        "python -m gaiden.chunk_book "
                        f"--book {book_code} --lang {_lang_fs(item.lang)} --normalized {norm_path} "
                        f"--out {chunk_dir} --target-chars 5600 --max-chars 6000"
                    )
                    if skip_existing and _chunks_exist(chunk_dir):
                        with log_path.open("w", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: chunks exist\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = "CHUNKS_EXIST"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    had_existing = _chunks_exist(chunk_dir)
                    if had_existing and not skip_existing:
                        _remove_existing_chunks(chunk_dir)

                    with log_path.open("a", encoding="utf-8") as log_file:
                        if had_existing:
                            log_file.write("OVERWRITTEN: existing output replaced\n")
                        _run_module(
                            log_file,
                            "gaiden.chunk_book",
                            [
                                "--book",
                                book_code,
                                "--lang",
                                _lang_fs(item.lang),
                                "--normalized",
                                str(norm_path),
                                "--out",
                                str(chunk_dir),
                                "--target-chars",
                                "5600",
                                "--max-chars",
                                "6000",
                            ],
                        )
                        manifest = _read_json(_chunks_manifest_path(chunk_dir))
                        if manifest:
                            log_file.write(
                                f"CHUNK_CHECK: {'OK' if manifest.get('check_ok') else 'FAIL'}\n"
                            )
                            if manifest.get("check_fail_reasons"):
                                log_file.write(
                                    "CHUNK_CHECK_REASONS: "
                                    + "; ".join(manifest["check_fail_reasons"])
                                    + "\n"
                                )

                    if not _chunks_exist(chunk_dir):
                        raise FileNotFoundError(f"Chunks not found: {chunk_dir}")
                    if manifest and manifest.get("check_ok") is False:
                        item.status = "FAILED"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "finished_at"])
                        continue

                    pipeline_state.chunked_at = timezone.now()
                    pipeline_state.current_stage = PipelineStage.CHUNKED
                    pipeline_state.save(update_fields=["chunked_at", "current_stage"])

                    item.status = "DONE"
                    item.finished_at = timezone.now()
                    item.overwrote = bool(had_existing)
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
                        norm_path = _normalized_path(book_code, source_lang)
                        if not norm_path.exists():
                            raw_path, raw_dir, raw_reason = _resolve_raw_path(book_code, source_lang)
                            if raw_reason:
                                with log_path.open("a", encoding="utf-8") as log_file:
                                    log_file.write(f"SKIP: {raw_reason}\n")
                                    log_file.write(f"RAW_DIR_SELECTED: {raw_dir}\n")
                                    log_file.write(f"RAW_SOURCE_FOUND: {raw_path or 'MISSING'}\n")
                                item.status = "SKIPPED"
                                item.skipped_reason = raw_reason
                                item.finished_at = timezone.now()
                                item.save(update_fields=["status", "skipped_reason", "finished_at"])
                                continue

                            with log_path.open("a", encoding="utf-8") as log_file:
                                log_file.write("AUTO: normalize before translate\n")
                                log_file.write(f"RAW_DIR_SELECTED: {raw_dir}\n")
                                log_file.write(f"RAW_SOURCE_FOUND: {raw_path}\n")
                                _run_module(log_file, "gaiden.normalize", [book_code, _lang_fs(source_lang)])
                                report = _read_json(_normalized_report_path(book_code, source_lang))
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
                            if report and report.get("status") == "FAIL":
                                raise RuntimeError("Normalize check failed during auto-dependency.")

                            if not norm_path.exists():
                                raise FileNotFoundError(f"Normalized output not found: {norm_path}")

                            texts, _ = EditionText.objects.get_or_create(edition=edition)
                            texts.normalized_path = str(norm_path)
                            texts.normalized_text = ""
                            texts.save(update_fields=["normalized_path", "normalized_text", "updated_at"])
                            pipeline_state.normalized_at = timezone.now()
                            pipeline_state.current_stage = PipelineStage.NORMALIZED
                            pipeline_state.save(update_fields=["normalized_at", "current_stage"])

                        chunk_dir = _chunks_dir(book_code, source_lang)
                        if not _chunks_exist(chunk_dir):
                            with log_path.open("a", encoding="utf-8") as log_file:
                                log_file.write("AUTO: chunk before translate\n")
                                _run_module(
                                    log_file,
                                    "gaiden.chunk_book",
                                    [
                                        "--book",
                                        book_code,
                                        "--lang",
                                        _lang_fs(source_lang),
                                        "--normalized",
                                        str(norm_path),
                                        "--out",
                                        str(chunk_dir),
                                        "--target-chars",
                                        "5600",
                                        "--max-chars",
                                        "6000",
                                    ],
                                )
                                manifest = _read_json(_chunks_manifest_path(chunk_dir))
                                if manifest:
                                    log_file.write(
                                        f"CHUNK_CHECK: {'OK' if manifest.get('check_ok') else 'FAIL'}\n"
                                    )
                                    if manifest.get("check_fail_reasons"):
                                        log_file.write(
                                            "CHUNK_CHECK_REASONS: "
                                            + "; ".join(manifest["check_fail_reasons"])
                                            + "\n"
                                        )
                            if manifest and manifest.get("check_ok") is False:
                                raise RuntimeError("Chunk check failed during auto-dependency.")
                            if not _chunks_exist(chunk_dir):
                                raise FileNotFoundError(f"Chunks not found: {chunk_dir}")
                            pipeline_state.chunked_at = timezone.now()
                            pipeline_state.current_stage = PipelineStage.CHUNKED
                            pipeline_state.save(update_fields=["chunked_at", "current_stage"])

                    if skip_existing and merge_path.exists():
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: output exists\n")
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
                        if had_existing:
                            log_file.write("NOTE: overwrite existing output\n")
                        log_file.flush()
                        with redirect_stdout(log_file), redirect_stderr(log_file):
                            run_translate_with_contract(
                                contract_path,
                                chunk_dir_override=chunk_dir,
                                out_dir_override=out_dir,
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
                            log_file.write("SKIP: missing merge_translate\n")
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
