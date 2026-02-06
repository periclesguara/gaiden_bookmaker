from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from gaiden.translate import run_translate_with_contract
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


def _translate_paths(book_id: int, lang: str) -> tuple[Path, Path, Path]:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    chunk_dir = data_dir / "chunks" / f"book_{book_id:04d}" / "en"
    out_dir = data_dir / "translated" / f"book_{book_id:04d}" / _lang_dir(lang)
    merge_path = out_dir / f"merge_translate_{_lang_dir(lang)}.txt"
    return chunk_dir, out_dir, merge_path


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
                item.skipped_reason = "INVALID_BOOK_CODE"
                item.save(update_fields=["status", "finished_at", "skipped_reason"])
                any_fail = True
                if stop_on_error:
                    break
                continue

            try:
                command_line = "n/a"
                chunk_dir, out_dir, merge_path = _translate_paths(book_id, item.lang)
                item.out_path = str(merge_path)
                item.save(update_fields=["out_path"])

                command_line = (
                    "run_translate_with_contract("
                    f"contract={_resolve_contract_path(item.lang)}, "
                    f"chunk_dir={chunk_dir}, out_dir={out_dir})"
                )

                if dry_run:
                    with log_path.open("w", encoding="utf-8") as log_file:
                        log_file.write(f"COMMAND: {command_line}\n")
                        log_file.write("DRY-RUN: no execution\n")
                    item.status = "SKIPPED"
                    item.skipped_reason = "DRY_RUN"
                    item.finished_at = timezone.now()
                    item.save(update_fields=["status", "skipped_reason", "finished_at"])
                    continue

                if skip_existing and merge_path.exists():
                    with log_path.open("w", encoding="utf-8") as log_file:
                        log_file.write(f"COMMAND: {command_line}\n")
                        log_file.write("SKIP: output exists\n")
                    item.status = "SKIPPED"
                    item.skipped_reason = "OUTPUT_EXISTS"
                    item.finished_at = timezone.now()
                    item.save(update_fields=["status", "skipped_reason", "finished_at"])
                    continue

                if not chunk_dir.exists():
                    raise FileNotFoundError(f"Chunks not found: {chunk_dir}")

                contract_path = _resolve_contract_path(item.lang)
                had_existing = merge_path.exists()

                with log_path.open("w", encoding="utf-8") as log_file:
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
                item.skipped_reason = "STOP_ON_ERROR"
                item.save(update_fields=["status", "finished_at", "skipped_reason"])

        run.finished_at = timezone.now()
        run.status = "FAILED" if any_fail else "DONE"
        run.save(update_fields=["status", "finished_at"])

        self.stdout.write(self.style.SUCCESS(f"Run {run.id} finalizado: {run.status}"))
