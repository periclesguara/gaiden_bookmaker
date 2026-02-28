import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from editorial.models import Edition, EditionText
from pipeline.services import utils


class CanonicalFlowError(RuntimeError):
    """Raised when canonical flow preconditions are not met."""


def project_root() -> Path:
    return Path(settings.BASE_DIR).parent


def _utc_stamp() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")


def _to_rel(path: Path) -> str:
    root = project_root()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _abs_from_rel_or_abs(path_value: str) -> Path:
    p = Path(path_value)
    if p.is_absolute():
        return p
    return project_root() / p


def _sha256_bytes(data: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(data)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as handle:
        handle.write(data)
    tmp.replace(path)


def _write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def _git_text(args: list[str]) -> str:
    result = subprocess.run(
        args,
        cwd=str(project_root()),
        capture_output=True,
        text=True,
        check=False,
    )
    return (result.stdout or result.stderr or "").strip()


def sync_edition_identity(edition: Edition) -> None:
    book_code = (edition.work.code or "").strip()
    lang_code = utils.normalize_lang(getattr(edition.language, "code", edition.language_code or "en"))
    changed_fields: list[str] = []
    if edition.book_id != book_code:
        edition.book_id = book_code
        changed_fields.append("book_id")
    if edition.lang != lang_code:
        edition.lang = lang_code
        changed_fields.append("lang")
    if changed_fields:
        changed_fields.append("updated_at")
        edition.save(update_fields=changed_fields)


def _raw_target_path(edition: Edition) -> Path:
    sync_edition_identity(edition)
    ext = Path(edition.raw_upload.name).suffix.lower() if edition.raw_upload and edition.raw_upload.name else ""
    if ext not in {".txt", ".md"}:
        source_format = (edition.work.source_format or "TXT").upper()
        ext = ".txt" if source_format == "TXT" else ".md"
    lang_code = edition.lang or utils.normalize_lang(edition.language.code)
    return project_root() / "data" / "raw" / edition.book_id / lang_code / f"source{ext}"


def _ingest_run_dir(book_id: str, stamp: str) -> Path:
    return project_root() / "docs" / "audit" / "runs" / f"{book_id}_ingest_{stamp}"


def _freeze_run_dir(book_id: str, stamp: str) -> Path:
    return project_root() / "docs" / "audit" / "runs" / f"{book_id}_freeze_{stamp}"


def _pretruth_freeze_run_dir(book_id: str, stamp: str) -> Path:
    return project_root() / "docs" / "audit" / "runs" / f"{book_id}_pretruth_freeze_{stamp}"


STATUS_ORDER = {
    Edition.STATUS_REGISTERED: 0,
    Edition.STATUS_UPLOADED: 1,
    Edition.STATUS_INGESTED: 2,
    Edition.STATUS_NORMALIZED: 3,
    Edition.STATUS_FIXED_TEXT: 4,
    Edition.STATUS_PRETRUTH_READY: 5,
    Edition.STATUS_CHUNKED: 6,
    Edition.STATUS_TRANSLATED: 7,
    Edition.STATUS_REFINED: 8,
    Edition.STATUS_POLISHED: 9,
    Edition.STATUS_CANONICAL_READY: 10,
}


def status_rank(status: str | None) -> int:
    key = (status or "").strip().upper()
    return STATUS_ORDER.get(key, -1)


def _write_ingest_receipts(
    *,
    run_dir: Path,
    book_id: str,
    lang: str,
    raw_source: str,
    raw_materialized: str,
    raw_sha256: str,
    created_utc: str,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_text(run_dir / "git_status.txt", _git_text(["git", "status", "-sb"]) + "\n")
    _write_text(run_dir / "git_head.txt", _git_text(["git", "rev-parse", "HEAD"]) + "\n")
    _write_text(run_dir / "SHA256SUMS.txt", f"{raw_sha256}  {raw_materialized}\n")
    manifest = {
        "book_id": book_id,
        "lang": lang,
        "raw_upload": raw_source,
        "raw_materialized_path": raw_materialized,
        "raw_sha256": raw_sha256,
        "created_utc": created_utc,
    }
    _write_text(run_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def materialize_raw(edition: Edition) -> dict:
    if not edition.raw_upload:
        raise CanonicalFlowError("No RAW upload found for this edition.")

    source_name = edition.raw_upload.name
    target_path = _raw_target_path(edition)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    hasher = hashlib.sha256()
    tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
    with edition.raw_upload.open("rb") as src, tmp_path.open("wb") as dst:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            hasher.update(chunk)
            dst.write(chunk)
    raw_sha = hasher.hexdigest()

    same_as_target = target_path.exists() and sha256_file(target_path) == raw_sha
    if same_as_target:
        tmp_path.unlink(missing_ok=True)
    else:
        tmp_path.replace(target_path)

    stamp = _utc_stamp()
    run_dir = _ingest_run_dir(edition.book_id or edition.work.code, stamp)
    _write_ingest_receipts(
        run_dir=run_dir,
        book_id=edition.book_id or edition.work.code,
        lang=edition.lang or utils.normalize_lang(edition.language.code),
        raw_source=source_name,
        raw_materialized=_to_rel(target_path),
        raw_sha256=raw_sha,
        created_utc=stamp,
    )

    now = timezone.now()
    edition.raw_materialized_path = _to_rel(target_path)
    edition.raw_source_path = _to_rel(target_path)
    edition.raw_sha256 = raw_sha
    edition.raw_materialized_at = now
    edition.status = Edition.STATUS_INGESTED
    sync_edition_identity(edition)
    edition.save(
        update_fields=[
            "raw_materialized_path",
            "raw_source_path",
            "raw_sha256",
            "raw_materialized_at",
            "status",
            "updated_at",
        ]
    )

    edition_text, _ = EditionText.objects.get_or_create(edition=edition)
    edition_text.raw_path = _to_rel(target_path)
    edition_text.save(update_fields=["raw_path", "updated_at"])

    return {
        "book_id": edition.book_id,
        "lang": edition.lang,
        "raw_source": source_name,
        "raw_materialized_path": _to_rel(target_path),
        "raw_sha256": raw_sha,
        "canonical_run_dir": _to_rel(run_dir),
        "skipped": same_as_target and edition.raw_sha256 == raw_sha,
    }


def resolve_truth_source_path(edition: Edition) -> Path | None:
    sync_edition_identity(edition)
    book_id = edition.book_id
    lang = edition.lang
    root = project_root()
    lang_upper = lang.upper()
    candidates = [
        root / "data" / "books" / book_id / lang / f"{book_id}_refine_clean.md",
        root / "data" / "books" / book_id / lang / f"{book_id}_refine_clean.txt",
        root / "data" / "books" / book_id / lang / "return" / f"{book_id}_refine_clean.md",
        root / "data" / "books" / book_id / lang / "return" / f"{book_id}_refine_clean.txt",
        root / "data" / "canonical" / book_id / lang / "canonical.md",
        root / "data" / "canonical" / book_id / lang / f"book.{lang}.v03.ready.md",
        root / "data" / "canonical" / book_id / lang / f"book.{lang}.v01.ready.md",
        root / "data" / "builds" / book_id / lang / "return" / f"{book_id}_refine_clean.md",
        root / "data" / "builds" / book_id / lang / "return" / f"{book_id}_refine_clean.txt",
        root / "data" / "builds" / book_id / lang_upper / "return" / f"{book_id}_refine_clean.md",
        root / "data" / "builds" / book_id / lang_upper / "return" / f"{book_id}_refine_clean.txt",
        root / "data" / "builds" / book_id / lang / "merge_refine.txt",
        root / "data" / "builds" / book_id / lang_upper / "merge_refine.txt",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def resolve_pretruth_source_path(edition: Edition) -> Path | None:
    sync_edition_identity(edition)
    root = project_root()
    book_id = edition.book_id
    lang = edition.lang
    lang_upper = lang.upper()
    candidates = [
        root / "data" / "normalized" / book_id / lang / "normalized.fixed.md",
        root / "data" / "normalized" / book_id / lang_upper / "normalized.fixed.md",
        root / "data" / "normalized" / book_id / lang / "normalized.md",
        root / "data" / "normalized" / book_id / lang_upper / "normalized.md",
        root / "data" / "raw" / book_id / lang / "source.txt",
        root / "data" / "raw" / book_id / lang / "source.md",
        root / "data" / "raw" / book_id / lang_upper / "source.txt",
        root / "data" / "raw" / book_id / lang_upper / "source.md",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _list_files_shas(files: list[Path]) -> tuple[list[str], list[str]]:
    rel_paths = [_to_rel(path) for path in files]
    sha_lines = [f"{sha256_file(path)}  {_to_rel(path)}" for path in files]
    return rel_paths, sha_lines


def _collect_image_and_cover_hashes(book_id: str, lang: str) -> dict:
    root = project_root()
    lang_upper = lang.upper()
    images_candidates = [
        root / "data" / "builds" / book_id / lang / "images",
        root / "data" / "builds" / book_id / lang_upper / "images",
    ]
    images_dir = next((p for p in images_candidates if p.is_dir()), images_candidates[0])
    cover_dir = root / "data" / "covers" / book_id / lang

    image_files = sorted(path for path in images_dir.glob("*.jpg") if path.is_file()) if images_dir.is_dir() else []
    cover_files = sorted(path for path in cover_dir.iterdir() if path.is_file()) if cover_dir.is_dir() else []

    image_list, image_sha = _list_files_shas(image_files)
    cover_list, cover_sha = _list_files_shas(cover_files)
    return {
        "images_dir": _to_rel(images_dir),
        "cover_dir": _to_rel(cover_dir),
        "images_list": image_list,
        "images_sha_lines": image_sha,
        "cover_list": cover_list,
        "cover_sha_lines": cover_sha,
    }


def _freeze_is_same_as_last(
    edition: Edition,
    truth_sha: str,
    images_sha_lines: list[str],
    cover_sha_lines: list[str],
) -> bool:
    if (edition.status or "").strip().upper() != Edition.STATUS_CANONICAL_READY:
        return False
    if not edition.canonical_run_dir or edition.truth_sha256 != truth_sha:
        return False
    run_dir = _abs_from_rel_or_abs(edition.canonical_run_dir)
    if not run_dir.exists():
        return False
    prev_images = (run_dir / "images_SHA256SUMS.txt")
    prev_cover = (run_dir / "cover_SHA256SUMS.txt")
    prev_images_lines = prev_images.read_text(encoding="utf-8").splitlines() if prev_images.exists() else []
    prev_cover_lines = prev_cover.read_text(encoding="utf-8").splitlines() if prev_cover.exists() else []
    return prev_images_lines == images_sha_lines and prev_cover_lines == cover_sha_lines


def _freeze_pretruth_is_same_as_last(edition: Edition, truth_sha: str) -> bool:
    if status_rank(edition.status) < status_rank(Edition.STATUS_PRETRUTH_READY):
        return False
    if edition.truth_sha256 != truth_sha:
        return False
    return True


def freeze_pretruth(edition: Edition) -> dict:
    source_path = resolve_pretruth_source_path(edition)
    if not source_path:
        raise CanonicalFlowError("No pretruth source found (normalized.fixed.md/normalized.md/source.*).")

    content = source_path.read_bytes()
    if not content.strip():
        raise CanonicalFlowError(f"Pretruth source is empty: {_to_rel(source_path)}")

    source_rel = _to_rel(source_path)
    truth_sha = _sha256_bytes(content)
    book_id = edition.book_id
    lang = edition.lang

    if _freeze_pretruth_is_same_as_last(edition, truth_sha):
        return {
            "book_id": book_id,
            "lang": lang,
            "truth_source": source_rel,
            "truth_path": edition.truth_path,
            "truth_sha256": truth_sha,
            "canonical_run_dir": edition.canonical_run_dir,
            "skipped": True,
        }

    stamp = _utc_stamp()
    run_dir = _pretruth_freeze_run_dir(book_id, stamp)
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_text(run_dir / "git_status.txt", _git_text(["git", "status", "-sb"]) + "\n")
    _write_text(run_dir / "git_head.txt", _git_text(["git", "rev-parse", "HEAD"]) + "\n")
    _write_text(run_dir / "SHA256SUMS.txt", f"{truth_sha}  {source_rel}\n")
    manifest = {
        "book_id": book_id,
        "lang": lang,
        "truth_source": source_rel,
        "truth_sha256": truth_sha,
        "mode": "pretruth",
        "created_utc": stamp,
    }
    _write_text(run_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    edition.truth_path = source_rel
    edition.truth_sha256 = truth_sha
    edition.truth_frozen_at = timezone.now()
    edition.canonical_run_dir = _to_rel(run_dir)
    edition.status = Edition.STATUS_PRETRUTH_READY
    edition.save(
        update_fields=[
            "truth_path",
            "truth_sha256",
            "truth_frozen_at",
            "canonical_run_dir",
            "status",
            "updated_at",
        ]
    )

    return {
        "book_id": book_id,
        "lang": lang,
        "truth_source": source_rel,
        "truth_path": source_rel,
        "truth_sha256": truth_sha,
        "canonical_run_dir": _to_rel(run_dir),
        "skipped": False,
    }


def freeze_canonical(edition: Edition) -> dict:
    source_path = resolve_truth_source_path(edition)
    if not source_path:
        raise CanonicalFlowError("No final text source found for canonical freeze.")

    content = source_path.read_bytes()
    if not content.strip():
        raise CanonicalFlowError(f"Truth source is empty: {_to_rel(source_path)}")

    truth_sha = _sha256_bytes(content)
    book_id = edition.book_id
    lang = edition.lang
    truth_target = project_root() / "data" / "books" / book_id / lang / f"{book_id}_refine_clean.md"
    assets = _collect_image_and_cover_hashes(book_id, lang)

    if _freeze_is_same_as_last(
        edition=edition,
        truth_sha=truth_sha,
        images_sha_lines=assets["images_sha_lines"],
        cover_sha_lines=assets["cover_sha_lines"],
    ):
        return {
            "book_id": book_id,
            "lang": lang,
            "truth_source": _to_rel(source_path),
            "truth_path": edition.truth_path,
            "truth_sha256": truth_sha,
            "canonical_run_dir": edition.canonical_run_dir,
            "skipped": True,
        }

    if not truth_target.exists() or truth_target.read_bytes() != content:
        _atomic_write_bytes(truth_target, content)

    stamp = _utc_stamp()
    run_dir = _freeze_run_dir(book_id, stamp)
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_text(run_dir / "git_status.txt", _git_text(["git", "status", "-sb"]) + "\n")
    _write_text(run_dir / "git_head.txt", _git_text(["git", "rev-parse", "HEAD"]) + "\n")
    _write_text(run_dir / "SHA256SUMS.txt", f"{truth_sha}  {_to_rel(truth_target)}\n")
    _write_text(run_dir / "images_list.txt", "\n".join(assets["images_list"]) + ("\n" if assets["images_list"] else ""))
    _write_text(
        run_dir / "images_SHA256SUMS.txt",
        "\n".join(assets["images_sha_lines"]) + ("\n" if assets["images_sha_lines"] else ""),
    )
    _write_text(run_dir / "cover_list.txt", "\n".join(assets["cover_list"]) + ("\n" if assets["cover_list"] else ""))
    _write_text(
        run_dir / "cover_SHA256SUMS.txt",
        "\n".join(assets["cover_sha_lines"]) + ("\n" if assets["cover_sha_lines"] else ""),
    )
    manifest = {
        "book_id": book_id,
        "lang": lang,
        "truth_file": _to_rel(truth_target),
        "truth_source": _to_rel(source_path),
        "truth_sha256": truth_sha,
        "images_dir": assets["images_dir"],
        "cover_dir": assets["cover_dir"],
        "created_utc": stamp,
    }
    _write_text(run_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    edition.truth_path = _to_rel(truth_target)
    edition.truth_sha256 = truth_sha
    edition.truth_frozen_at = timezone.now()
    edition.canonical_run_dir = _to_rel(run_dir)
    edition.status = Edition.STATUS_CANONICAL_READY
    edition.save(
        update_fields=[
            "truth_path",
            "truth_sha256",
            "truth_frozen_at",
            "canonical_run_dir",
            "status",
            "updated_at",
        ]
    )

    return {
        "book_id": book_id,
        "lang": lang,
        "truth_source": _to_rel(source_path),
        "truth_path": _to_rel(truth_target),
        "truth_sha256": truth_sha,
        "canonical_run_dir": _to_rel(run_dir),
        "skipped": False,
    }
