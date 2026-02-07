from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from gaiden.translate import run_translate_with_contract
from gaiden.normalize import normalize_text_policy_v1_en_clean
from gaiden.chunk_book import chunk_book_en
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


def _translate_paths(book_id: int, lang: str) -> tuple[Path, Path, Path]:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    chunk_dir = data_dir / "chunks" / f"book_{book_id:04d}" / "en"
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
    lang_code = utils.normalize_lang(lang)
    return data_dir / "normalized" / f"{book_code}_{lang_code}_v2.txt"


def _normalized_candidates(book_code: str, lang: str) -> list[Path]:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    lang_code = utils.normalize_lang(lang)
    lang_dir = _lang_dir(lang_code)
    name_v2 = f"{book_code}_{lang_code}_v2.txt"
    name_v1 = f"{book_code}_{lang_code}_v1.txt"
    return [
        data_dir / "normalized" / book_code / lang_dir / name_v2,
        data_dir / "normalized" / name_v2,
        data_dir / "normalized" / book_code / lang_dir / name_v1,
        data_dir / "normalized" / name_v1,
    ]


def _resolve_normalized_path(book_code: str, lang: str) -> Path | None:
    for candidate in _normalized_candidates(book_code, lang):
        if candidate.exists():
            return candidate
    return None


def _raw_path(book_code: str, lang: str, source_format: str) -> Path:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    lang_dir = _lang_dir(lang)
    ext = "txt" if source_format.upper() == "TXT" else "md"
    return data_dir / "raw" / book_code / lang_dir / f"source.{ext}"


def _chunks_dir(book_id: int) -> Path:
    data_dir = Path(settings.BASE_DIR).parent / "data"
    return data_dir / "chunks" / f"book_{book_id:04d}" / "en"


def _chunks_manifest_path(chunks_dir: Path) -> Path:
    return chunks_dir / "manifest.json"


def _chunks_exist(chunks_dir: Path) -> bool:
    return _chunks_manifest_path(chunks_dir).exists()


def _remove_existing_chunks(chunks_dir: Path) -> None:
    if not chunks_dir.exists():
        return
    for path in chunks_dir.glob("ch_*.txt"):
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
                if run.action == "NORMALIZE":
                    norm_path = _normalized_path(item.book_code or f"book_{book_id:04d}", item.lang)
                    item.out_path = str(norm_path)
                    item.save(update_fields=["out_path"])
                    command_line = f"normalize(book={item.book_code}, lang={item.lang})"
                elif run.action == "CHUNK":
                    chunk_dir = _chunks_dir(book_id)
                    manifest_path = _chunks_manifest_path(chunk_dir)
                    item.out_path = str(manifest_path)
                    item.save(update_fields=["out_path"])
                    normalized_path = _resolve_normalized_path(item.book_code or f"book_{book_id:04d}", "en")
                    command_line = (
                        "python -m gaiden.chunk_book "
                        f"--book {item.book_code} --normalized {normalized_path or 'MISSING'} "
                        f"--out {chunk_dir} --max-tokens 1500"
                    )
                elif run.action == "TRANSLATE":
                    chunk_dir, out_dir, merge_path = _translate_paths(book_id, item.lang)
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
                    item.skipped_reason = "DRY_RUN"
                    item.finished_at = timezone.now()
                    item.save(update_fields=["status", "skipped_reason", "finished_at"])
                    continue

                if run.action == "NORMALIZE":
                    if utils.normalize_lang(item.lang) != "en":
                        with log_path.open("w", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: normalize only supports EN\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = "NORMALIZE_ONLY_EN"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    edition = Edition.objects.select_related("work", "language").filter(
                        work__code=item.book_code,
                        language__code="en",
                    ).first()
                    if not edition:
                        raise FileNotFoundError("Edition not found for normalize.")

                    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)

                    source_format = edition.work.source_format or "TXT"
                    raw_path = _raw_path(item.book_code or f"book_{book_id:04d}", "en", source_format)
                    if not raw_path.exists():
                        with log_path.open("w", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: missing RAW source\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = "MISSING_RAW"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    norm_path = _normalized_path(item.book_code or f"book_{book_id:04d}", "en")
                    if skip_existing and norm_path.exists():
                        with log_path.open("w", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: output exists\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = "OUTPUT_EXISTS"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    had_existing = norm_path.exists()
                    raw_text = raw_path.read_text(encoding="utf-8")
                    normalized, stats = normalize_text_policy_v1_en_clean(raw_text)

                    norm_path.parent.mkdir(parents=True, exist_ok=True)
                    with log_path.open("w", encoding="utf-8") as log_file:
                        log_file.write(f"COMMAND: {command_line}\n")
                        log_file.write(
                            f"[NORMALIZE] Gutenberg markers: START={'YES' if stats['start_found'] else 'NO'} "
                            f"END={'YES' if stats['end_found'] else 'NO'}\n"
                        )
                        log_file.write(f"[NORMALIZE] Head removed: {stats['head_removed']} lines\n")
                        log_file.write(f"[NORMALIZE] Tail removed: {stats['tail_removed']} lines\n")
                        if had_existing:
                            log_file.write("NOTE: overwrite existing output\n")
                        log_file.write(f"[NORMALIZE] Output: {norm_path}\n")
                        log_file.flush()
                        with redirect_stdout(log_file), redirect_stderr(log_file):
                            norm_path.write_text(normalized, encoding="utf-8")

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
                    if utils.normalize_lang(item.lang) != "en":
                        with log_path.open("w", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: chunk only supports EN\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = "CHUNK_ONLY_EN"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    edition = Edition.objects.select_related("work", "language").filter(
                        work__code=item.book_code,
                        language__code="en",
                    ).first()
                    if not edition:
                        raise FileNotFoundError("Edition not found for chunk.")

                    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)
                    norm_path = _resolve_normalized_path(item.book_code or f"book_{book_id:04d}", "en")
                    if not norm_path:
                        source_format = edition.work.source_format or "TXT"
                        raw_path = _raw_path(item.book_code or f"book_{book_id:04d}", "en", source_format)
                        if not raw_path.exists():
                            with log_path.open("w", encoding="utf-8") as log_file:
                                log_file.write(f"COMMAND: {command_line}\n")
                                log_file.write("ERROR: missing RAW source for normalize\n")
                            item.status = "FAILED"
                            item.skipped_reason = "MISSING_RAW"
                            item.finished_at = timezone.now()
                            item.save(update_fields=["status", "skipped_reason", "finished_at"])
                            continue

                        raw_text = raw_path.read_text(encoding="utf-8")
                        normalized, stats = normalize_text_policy_v1_en_clean(raw_text)
                        norm_path = _normalized_path(item.book_code or f"book_{book_id:04d}", "en")
                        norm_path.parent.mkdir(parents=True, exist_ok=True)
                        with log_path.open("w", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("AUTO: normalize before chunk\n")
                            log_file.write(
                                f"[NORMALIZE] Gutenberg markers: START={'YES' if stats['start_found'] else 'NO'} "
                                f"END={'YES' if stats['end_found'] else 'NO'}\n"
                            )
                            log_file.write(f"[NORMALIZE] Head removed: {stats['head_removed']} lines\n")
                            log_file.write(f"[NORMALIZE] Tail removed: {stats['tail_removed']} lines\n")
                            log_file.write(f"[NORMALIZE] Output: {norm_path}\n")
                            log_file.flush()
                            with redirect_stdout(log_file), redirect_stderr(log_file):
                                norm_path.write_text(normalized, encoding="utf-8")

                        texts, _ = EditionText.objects.get_or_create(edition=edition)
                        texts.normalized_path = str(norm_path)
                        texts.normalized_text = ""
                        texts.save(update_fields=["normalized_path", "normalized_text", "updated_at"])
                        pipeline_state.normalized_at = timezone.now()
                        pipeline_state.current_stage = PipelineStage.NORMALIZED
                        pipeline_state.save(update_fields=["normalized_at", "current_stage"])

                    chunk_dir = _chunks_dir(book_id)
                    command_line = (
                        "python -m gaiden.chunk_book "
                        f"--book {item.book_code} --normalized {norm_path} "
                        f"--out {chunk_dir} --max-tokens 1500"
                    )
                    if skip_existing and _chunks_exist(chunk_dir):
                        with log_path.open("w", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: output exists\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = "OUTPUT_EXISTS"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    had_existing = _chunks_exist(chunk_dir)
                    if had_existing and not skip_existing:
                        _remove_existing_chunks(chunk_dir)

                    with log_path.open("a", encoding="utf-8") as log_file:
                        log_file.write(f"COMMAND: {command_line}\n")
                        if had_existing:
                            log_file.write("NOTE: overwrite existing output\n")
                        log_file.flush()
                        with redirect_stdout(log_file), redirect_stderr(log_file):
                            manifest = chunk_book_en(
                                item.book_code or f"book_{book_id:04d}",
                                norm_path,
                                chunk_dir,
                                max_tokens=1500,
                            )

                    if not _chunks_exist(chunk_dir):
                        raise FileNotFoundError(f"Chunks not found: {chunk_dir}")

                    pipeline_state.chunked_at = timezone.now()
                    pipeline_state.current_stage = PipelineStage.CHUNKED
                    pipeline_state.save(update_fields=["chunked_at", "current_stage"])

                    try:
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write(
                                f"[CHUNK] chapters={len(manifest.get('chapters', []))} "
                                f"chunks={manifest.get('total_chunks', 0)} "
                                f"max_est_tokens={manifest.get('max_est_tokens', 0)} "
                                f"oversize_splits={manifest.get('oversize_splits', 0)}\n"
                            )
                            log_file.write(f"[CHUNK] Output: {chunk_dir}\n")
                    except Exception:
                        pass

                    item.status = "DONE"
                    item.finished_at = timezone.now()
                    item.overwrote = bool(had_existing)
                    item.save(update_fields=["status", "finished_at", "overwrote"])

                elif run.action == "TRANSLATE":
                    edition = Edition.objects.select_related("work", "language").filter(
                        work__code=item.book_code,
                        language__code="en",
                    ).first()
                    if not edition:
                        raise FileNotFoundError("Edition not found for translate.")

                    pipeline_state, _ = EditionPipeline.objects.get_or_create(edition=edition)

                    if not dry_run:
                        norm_path = _normalized_path(item.book_code or f"book_{book_id:04d}", "en")
                        if not norm_path.exists():
                            source_format = edition.work.source_format or "TXT"
                            raw_path = _raw_path(item.book_code or f"book_{book_id:04d}", "en", source_format)
                            if not raw_path.exists():
                                raise FileNotFoundError(f"RAW not found: {raw_path}")

                            raw_text = raw_path.read_text(encoding="utf-8")
                            normalized, stats = normalize_text_policy_v1_en_clean(raw_text)

                            norm_path.parent.mkdir(parents=True, exist_ok=True)
                            with log_path.open("a", encoding="utf-8") as log_file:
                                log_file.write("AUTO: normalize before translate\n")
                                log_file.write(
                                    f"[NORMALIZE] Gutenberg markers: START={'YES' if stats['start_found'] else 'NO'} "
                                    f"END={'YES' if stats['end_found'] else 'NO'}\n"
                                )
                                log_file.write(f"[NORMALIZE] Head removed: {stats['head_removed']} lines\n")
                                log_file.write(f"[NORMALIZE] Tail removed: {stats['tail_removed']} lines\n")
                                log_file.write(f"[NORMALIZE] Output: {norm_path}\n")
                                log_file.flush()
                                with redirect_stdout(log_file), redirect_stderr(log_file):
                                    norm_path.write_text(normalized, encoding="utf-8")

                            texts, _ = EditionText.objects.get_or_create(edition=edition)
                            texts.normalized_path = str(norm_path)
                            texts.normalized_text = ""
                            texts.save(update_fields=["normalized_path", "normalized_text", "updated_at"])
                            pipeline_state.normalized_at = timezone.now()
                            pipeline_state.current_stage = PipelineStage.NORMALIZED
                            pipeline_state.save(update_fields=["normalized_at", "current_stage"])

                        chunk_dir = _chunks_dir(book_id)
                        if not _chunks_exist(chunk_dir):
                            with log_path.open("a", encoding="utf-8") as log_file:
                                log_file.write("AUTO: chunk before translate\n")
                                log_file.flush()
                                with redirect_stdout(log_file), redirect_stderr(log_file):
                                    chunk_book_en(
                                        item.book_code or f"book_{book_id:04d}",
                                        norm_path,
                                        chunk_dir,
                                        max_tokens=1500,
                                    )
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
                        item.skipped_reason = "OUTPUT_EXISTS"
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
                        item.skipped_reason = "MISSING_MERGE_TRANSLATE"
                        item.finished_at = timezone.now()
                        item.save(update_fields=["status", "skipped_reason", "finished_at"])
                        continue

                    if skip_existing and split_dir.exists():
                        with log_path.open("w", encoding="utf-8") as log_file:
                            log_file.write(f"COMMAND: {command_line}\n")
                            log_file.write("SKIP: output exists\n")
                        item.status = "SKIPPED"
                        item.skipped_reason = "OUTPUT_EXISTS"
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
                item.skipped_reason = "STOP_ON_ERROR"
                item.save(update_fields=["status", "finished_at", "skipped_reason"])

        run.finished_at = timezone.now()
        run.status = "FAILED" if any_fail else "DONE"
        run.save(update_fields=["status", "finished_at"])

        self.stdout.write(self.style.SUCCESS(f"Run {run.id} finalizado: {run.status}"))
