from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import difflib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.conf import settings

from gaiden.translate_artifacts import list_canonical_artifacts, resolve_active_or_latest

from . import utils

DEFAULT_MIN_BYTES = 10 * 1024
VALID_MODES = {"default", "full"}
PLACEHOLDER_SNIPPETS = (
    "output must contain",
    "here is the rewritten text",
    "here's the rewritten text",
    "(truncated)",
    "[truncated]",
    "as an ai",
)
OK_TRANSLATE_STATUSES = {"ok", "ok_official", "ok_fallback"}


def project_root() -> Path:
    return Path(settings.BASE_DIR).parent


def books_lang_root(book_code: str, language: str) -> Path:
    return project_root() / "data" / "books" / book_code / utils.normalize_lang(language)


def canonical_text_dir(book_code: str, language: str) -> Path:
    return books_lang_root(book_code, language) / "canonical" / "text"


def canonical_images_dir(book_code: str, language: str) -> Path:
    return books_lang_root(book_code, language) / "canonical" / "images"


def canonical_active_txt_path(book_code: str, language: str) -> Path:
    return canonical_text_dir(book_code, language) / "active.txt"


def canonical_active_json_path(book_code: str, language: str) -> Path:
    return canonical_text_dir(book_code, language) / "active.json"


def canonical_active_md_path(book_code: str, language: str) -> Path:
    return canonical_text_dir(book_code, language) / "active.md"


def canonical_build_source_md_path(book_code: str, language: str) -> Path:
    return canonical_text_dir(book_code, language) / "build_source.md"


def canonical_history_dir(book_code: str, language: str) -> Path:
    return canonical_text_dir(book_code, language) / "history"


def translate_runs_root(book_code: str, language: str) -> Path:
    return books_lang_root(book_code, language) / "runs" / "translate"


def translate_run_dir(book_code: str, language: str, run_id: str) -> Path:
    return translate_runs_root(book_code, language) / run_id


def latest_translate_run_dir(book_code: str, language: str) -> Path | None:
    root = translate_runs_root(book_code, language)
    if not root.exists():
        return None
    runs = [p for p in root.iterdir() if p.is_dir()]
    if not runs:
        return None
    runs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return runs[0]


def _squash_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _similarity_ratio_text(a: str, b: str) -> float:
    return difflib.SequenceMatcher(a=_squash_text(a), b=_squash_text(b)).ratio()


def _resolve_maybe_relative(path_str: str | None) -> Path | None:
    raw = str(path_str or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path
    return project_root() / path


def _translated_root_for(book_code: str, language: str) -> list[Path]:
    root = project_root() / "data" / "translated" / book_code
    return [root / alias for alias in _translated_aliases(language)]


def _latest_translate_report(book_code: str, language: str) -> Path | None:
    candidates: list[Path] = []
    run_dir = latest_translate_run_dir(book_code, language)
    if run_dir:
        for name in ("agent_translate_run_report.json", "translate_safe_run_report.json"):
            candidates.append(run_dir / name)

    for out_dir in _translated_root_for(book_code, language):
        for name in ("agent_translate_run_report.json", "translate_safe_run_report.json"):
            candidates.append(out_dir / name)

    existing: list[Path] = [p for p in candidates if p.exists() and p.is_file()]
    if not existing:
        return None
    existing.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return existing[0]


def translate_run_integrity(book_code: str, language: str) -> dict[str, Any]:
    report_path = _latest_translate_report(book_code, language)
    if not report_path:
        return {
            "ok": False,
            "reason": "missing_translate_report",
            "report_path": None,
            "preflight_ok": None,
            "status": None,
            "artifact_path": None,
            "artifact_sha256": None,
        }
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        return {
            "ok": False,
            "reason": f"invalid_translate_report_json:{type(exc).__name__}",
            "report_path": str(report_path),
            "preflight_ok": None,
            "status": None,
            "artifact_path": None,
            "artifact_sha256": None,
        }

    preflight_ok = payload.get("preflight_ok")
    status_raw = str(payload.get("status") or "").strip().lower()
    status = status_raw or None
    out_dir = _resolve_maybe_relative(payload.get("out_dir"))
    artifact_sha = str(payload.get("artifact_sha256") or "").strip() or None

    artifact_path: Path | None = None
    for key in ("artifact_path", "merged_txt"):
        candidate = _resolve_maybe_relative(payload.get(key))
        if candidate and candidate.exists() and candidate.is_file():
            artifact_path = candidate
            break
    if artifact_path is None:
        final = payload.get("final")
        if isinstance(final, dict):
            candidate = _resolve_maybe_relative(final.get("merged_txt"))
            if candidate and candidate.exists() and candidate.is_file():
                artifact_path = candidate
    if artifact_path is None:
        artifact_name = str(payload.get("artifact_filename") or payload.get("artifact") or "").strip()
        if out_dir and artifact_name:
            candidate = out_dir / artifact_name
            if candidate.exists() and candidate.is_file():
                artifact_path = candidate
    if artifact_path is None and out_dir:
        default_clean = out_dir / "merge_refine_clean.txt"
        if default_clean.exists() and default_clean.is_file():
            artifact_path = default_clean

    if artifact_sha is None and artifact_path:
        artifact_sha = sha256_file(artifact_path)

    if preflight_ok is not True:
        return {
            "ok": False,
            "reason": "preflight_not_ok",
            "report_path": str(report_path),
            "preflight_ok": preflight_ok,
            "status": status,
            "artifact_path": str(artifact_path) if artifact_path else None,
            "artifact_sha256": artifact_sha,
        }
    if status_raw not in OK_TRANSLATE_STATUSES:
        return {
            "ok": False,
            "reason": f"status_not_ok:{status_raw or 'missing'}",
            "report_path": str(report_path),
            "preflight_ok": preflight_ok,
            "status": status,
            "artifact_path": str(artifact_path) if artifact_path else None,
            "artifact_sha256": artifact_sha,
        }
    if artifact_path is None:
        return {
            "ok": False,
            "reason": "missing_artifact_path",
            "report_path": str(report_path),
            "preflight_ok": preflight_ok,
            "status": status,
            "artifact_path": None,
            "artifact_sha256": artifact_sha,
        }

    return {
        "ok": True,
        "reason": "ok",
        "report_path": str(report_path),
        "preflight_ok": preflight_ok,
        "status": status,
        "artifact_path": str(artifact_path),
        "artifact_sha256": artifact_sha,
    }


def _reconstruct_from_chunks(book_code: str, language: str) -> str:
    source_lang = utils.normalize_lang(language)
    chunk_dir = project_root() / "data" / "chunks" / book_code / source_lang
    chunks = sorted(chunk_dir.glob("ch_*_chunk_*.txt"))
    if not chunks:
        return ""
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in chunks)


def _find_tainted_marker(book_code: str, language: str) -> Path | None:
    for translated_dir in _translated_root_for(book_code, language):
        marker = translated_dir / "TAINTED_NO_AGENT.txt"
        if marker.exists():
            return marker
    return None


def _no_op_gate(book_code: str, language: str, clean_path: Path) -> tuple[bool, str, float]:
    tainted_marker = clean_path.parent / "TAINTED_NO_AGENT.txt"
    if not tainted_marker.exists():
        tainted_marker = _find_tainted_marker(book_code, language) or tainted_marker
    if tainted_marker.exists() and os.getenv("FORCE_PROMOTE", "") != "1":
        return True, "tainted_marker_present", 1.0

    normalized_lang = utils.normalize_lang(language)
    merged_candidates = [
        clean_path.parent / f"{book_code}_{normalized_lang}_merged_v1.txt",
        clean_path.parent / f"{book_code}_{normalized_lang}_modern_merged_v1.txt",
        clean_path.parent / f"{book_code}_en_modern_merged_v1.txt",
    ]
    merged_candidates.extend(sorted(clean_path.parent.glob(f"{book_code}*_merged_v1.txt")))

    baseline_text = ""
    for candidate in merged_candidates:
        if candidate.exists() and candidate.is_file() and candidate.resolve() != clean_path.resolve():
            baseline_text = candidate.read_text(encoding="utf-8", errors="replace")
            break
    if not baseline_text:
        baseline_text = _reconstruct_from_chunks(book_code, language)
    if not baseline_text:
        return False, "no_baseline", 0.0

    clean_text = clean_path.read_text(encoding="utf-8", errors="replace")
    ratio = _similarity_ratio_text(baseline_text, clean_text)
    if ratio >= 0.9999:
        return True, "clean_noop_detected", ratio
    return False, "ok", ratio


def normalize_mode(mode: str | None, *, default: str = "full") -> str:
    raw = (mode or default).strip().lower()
    if raw in {"automatic", "auto"}:
        return "full"
    if raw in VALID_MODES:
        return raw
    return default


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(str(path) + ".tmp")
    fd = None
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        os.write(fd, data)
        os.fsync(fd)
        os.close(fd)
        fd = None
        os.replace(tmp_path, path)

        dir_fd = None
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            if dir_fd is not None:
                os.close(dir_fd)
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def text_stats(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8", errors="strict")
    lines = text.splitlines()
    para_count = 0
    in_para = False
    for line in lines:
        if line.strip():
            if not in_para:
                para_count += 1
                in_para = True
        else:
            in_para = False
    return {
        "bytes": path.stat().st_size,
        "chars": len(text),
        "lines": len(lines),
        "paragraphs": para_count,
    }


def _min_bytes_from_env(default: int = DEFAULT_MIN_BYTES) -> int:
    raw = str(os.getenv("GAIDEN_CANONICAL_TEXT_MIN_BYTES", str(default))).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < 1:
        return default
    return value


def validate_clean_text(
    path: Path,
    *,
    min_bytes: int | None = None,
    source_stats: dict[str, int] | None = None,
    enforce_ratio: bool = True,
    ratio_min: float = 0.90,
    ratio_max: float = 1.10,
    min_line_ratio: float = 0.85,
) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"missing_file:{path}"]
    if not path.is_file():
        return [f"not_a_file:{path}"]

    threshold = _min_bytes_from_env() if min_bytes is None else int(min_bytes)
    size = path.stat().st_size
    if size <= threshold:
        errors.append(f"size_below_threshold:{size}<={threshold}")

    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except Exception as exc:
        return [f"read_error:{type(exc).__name__}:{exc}"]

    if not text.strip():
        errors.append("empty_after_strip")
        return errors
    if not text.endswith("\n"):
        errors.append("missing_trailing_newline")

    lowered = text.lower()
    for marker in PLACEHOLDER_SNIPPETS:
        if marker in lowered:
            errors.append(f"placeholder:{marker}")
    if re.search(r"\.\.\.(\s*\.\.\.){2,}", text):
        errors.append("placeholder:repeated_ellipsis")

    # Tail should end in punctuation, quote or closing parenthesis.
    tail = text.rstrip()
    if tail and tail[-1] not in {".", "!", "?", "\"", "'", "”", "’", ")"}:
        errors.append("tail_not_clean")

    if source_stats and enforce_ratio:
        out_stats = {
            "chars": len(text),
            "lines": len(text.splitlines()),
            "paragraphs": max(1, sum(1 for block in re.split(r"\n\s*\n", text.strip()) if block.strip())),
        }
        in_chars = int(source_stats.get("chars", 0))
        in_lines = int(source_stats.get("lines", 0))
        in_paragraphs = int(source_stats.get("paragraphs", 0))

        if in_chars > 0:
            ratio = out_stats["chars"] / in_chars
            if ratio < ratio_min or ratio > ratio_max:
                errors.append(f"chars_ratio_out_of_band:{ratio:.4f}")
        if in_lines > 0:
            line_ratio = out_stats["lines"] / in_lines
            if line_ratio < min_line_ratio:
                errors.append(f"line_ratio_below_threshold:{line_ratio:.4f}")
        if in_paragraphs > 0:
            tolerated = max(2, int(in_paragraphs * 0.10))
            if abs(out_stats["paragraphs"] - in_paragraphs) > tolerated:
                errors.append(
                    f"paragraph_delta_too_high:{out_stats['paragraphs']}!={in_paragraphs}±{tolerated}"
                )

    return errors


def resolve_canonical_text(book_code: str, language: str) -> Path | None:
    active = canonical_active_txt_path(book_code, language)
    if active.exists() and active.is_file():
        return active
    return None


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def canonical_status(book_code: str, language: str) -> dict[str, Any]:
    active = canonical_active_txt_path(book_code, language)
    meta_path = canonical_active_json_path(book_code, language)
    meta = _load_json(meta_path)
    exists = active.exists() and active.is_file()
    integrity = translate_run_integrity(book_code, language)
    tainted_marker = _find_tainted_marker(book_code, language)
    tainted = tainted_marker is not None and os.getenv("FORCE_PROMOTE", "") != "1"
    noop_ratio: float | None = None
    clean_noop_suspected = False

    candidate_clean: Path | None = None
    artifact_path = _resolve_maybe_relative(integrity.get("artifact_path"))
    if artifact_path and artifact_path.exists() and artifact_path.is_file():
        candidate_clean = artifact_path
    else:
        discovered = _discover_latest_clean_candidates(book_code, language)
        if discovered:
            candidate_clean = discovered[0][0]
    if candidate_clean and candidate_clean.exists() and candidate_clean.is_file():
        blocked, gate_reason, ratio = _no_op_gate(book_code, language, candidate_clean)
        if gate_reason != "no_baseline":
            noop_ratio = ratio
        clean_noop_suspected = blocked and gate_reason == "clean_noop_detected"

    status: dict[str, Any] = {
        "book_code": book_code,
        "language": utils.normalize_lang(language),
        "active_path": active,
        "active_json_path": meta_path,
        "exists": exists,
        "mode": (meta or {}).get("mode"),
        "status": (meta or {}).get("status"),
        "sha256": None,
        "size_bytes": 0,
        "promoted_at": (meta or {}).get("promoted_at"),
        "reason": None,
        "fasttrack_ready": False,
        "translate_run_ok": bool(integrity.get("ok")),
        "translate_run_reason": integrity.get("reason"),
        "translate_report_path": integrity.get("report_path"),
        "clean_noop_suspected": clean_noop_suspected,
        "clean_noop_ratio": noop_ratio,
        "tainted": tainted,
    }

    if exists:
        status["size_bytes"] = active.stat().st_size
        status["sha256"] = sha256_file(active)

    if not exists:
        status["reason"] = "missing_active_txt"
        return status
    if not meta:
        status["reason"] = "missing_active_json"
        return status
    if str(meta.get("status") or "").strip().lower() != "ok":
        status["reason"] = "active_json_status_not_ok"
        return status
    if normalize_mode(str(meta.get("mode") or ""), default="") not in VALID_MODES:
        status["reason"] = "active_json_mode_invalid"
        return status

    if not status["translate_run_ok"]:
        status["reason"] = f"translate_run_not_ok:{status['translate_run_reason']}"
        return status
    if status["clean_noop_suspected"]:
        status["reason"] = "clean_noop_detected"
        return status
    if status["tainted"]:
        status["reason"] = "tainted_marker_present"
        return status

    status["fasttrack_ready"] = True
    return status


def promote_clean_to_canonical(
    book_code: str,
    language: str,
    mode: str,
    clean_path: str | Path,
    *,
    meta: dict[str, Any] | None = None,
    source_stats: dict[str, int] | None = None,
    min_bytes: int | None = None,
    enforce_ratio: bool = True,
) -> dict[str, Any]:
    integrity = translate_run_integrity(book_code, language)
    if not integrity.get("ok"):
        raise ValueError(f"translate_run_not_ok:{integrity.get('reason')}")

    clean = Path(clean_path)
    blocked, gate_reason, gate_ratio = _no_op_gate(book_code, language, clean)
    if blocked:
        raise ValueError(f"{gate_reason}: similarity_ratio={gate_ratio:.6f}")
    noop_ratio: float | None = None if gate_reason == "no_baseline" else gate_ratio

    normalized_mode = normalize_mode(mode, default="full")
    errors = validate_clean_text(
        clean,
        min_bytes=min_bytes,
        source_stats=source_stats,
        enforce_ratio=enforce_ratio,
    )
    if errors:
        raise RuntimeError("CANONICAL_PROMOTE_FAILED: " + ";".join(errors))

    data = clean.read_bytes()
    if not data.endswith(b"\n"):
        data += b"\n"
    sha256 = _sha256_bytes(data)
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H-%M-%SZ")

    text_dir = canonical_text_dir(book_code, language)
    history_dir = canonical_history_dir(book_code, language)
    active_txt = canonical_active_txt_path(book_code, language)
    active_json = canonical_active_json_path(book_code, language)

    text_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)

    history_name = f"{stamp}_{normalized_mode}_merge_refine_clean.txt"
    history_path = history_dir / history_name
    _atomic_write_bytes(history_path, data)
    _atomic_write_bytes(active_txt, data)

    payload: dict[str, Any] = {
        "book_code": book_code,
        "language": utils.normalize_lang(language),
        "mode": normalized_mode,
        "status": "ok",
        "promoted_at": now.isoformat(),
        "source_path": str(clean),
        "source_name": clean.name,
        "active_path": str(active_txt),
        "history_path": str(history_path),
        "sha256": sha256,
        "size_bytes": len(data),
        "source_stats": _jsonable(source_stats or {}),
        "integrity_report_path": integrity.get("report_path"),
        "integrity_artifact_sha256": integrity.get("artifact_sha256"),
        "noop_similarity_ratio": noop_ratio,
    }
    for key, value in (meta or {}).items():
        payload[str(key)] = _jsonable(value)

    _atomic_write_text(active_json, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    return {
        "book_code": book_code,
        "language": utils.normalize_lang(language),
        "mode": normalized_mode,
        "active_path": active_txt,
        "active_json_path": active_json,
        "history_path": history_path,
        "sha256": sha256,
        "size_bytes": len(data),
    }


def _candidate_mode_from_name(name: str) -> str | None:
    lowered = name.lower()
    if "default" in lowered:
        return "default"
    if "full" in lowered or "automatic" in lowered:
        return "full"
    return None


def _translated_aliases(language: str) -> list[str]:
    raw = (language or "").strip()
    normalized = utils.normalize_lang(language)
    aliases = [x for x in [raw, normalized] if x]
    if normalized == "en":
        aliases.extend(["en_modern", "enmodern"])
    dedup: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        if alias in seen:
            continue
        seen.add(alias)
        dedup.append(alias)
    return dedup


def _discover_latest_clean_candidates(book_code: str, language: str) -> list[tuple[Path, str | None]]:
    candidates: list[tuple[Path, str | None]] = []
    seen: set[str] = set()

    runs_root = translate_runs_root(book_code, language)
    if runs_root.exists():
        for path in runs_root.glob("*/outputs/*merge_refine_clean*.txt"):
            if not path.is_file():
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            candidates.append((path, _candidate_mode_from_name(path.name)))

    data_root = project_root() / "data" / "translated"
    for alias in _translated_aliases(language):
        out_dir = data_root / book_code / alias
        if not out_dir.exists():
            continue

        active = resolve_active_or_latest(out_dir, book_code, alias)
        if active and active.exists():
            key = str(active.resolve())
            if key not in seen:
                seen.add(key)
                candidates.append((active, _candidate_mode_from_name(active.name)))

        for path in list_canonical_artifacts(out_dir, book_code, alias):
            if not path.exists():
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            candidates.append((path, _candidate_mode_from_name(path.name)))

        for path in out_dir.glob("*merge_refine_clean*.txt"):
            if not path.is_file():
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            candidates.append((path, _candidate_mode_from_name(path.name)))

    return candidates


def repromote_latest(
    book_code: str,
    language: str,
    *,
    preferred_mode: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preferred = normalize_mode(preferred_mode, default="default")
    candidates = _discover_latest_clean_candidates(book_code, language)
    if not candidates:
        raise FileNotFoundError(
            f"No clean candidate found to repromote for {book_code}/{utils.normalize_lang(language)}."
        )

    def _rank(item: tuple[Path, str | None]) -> tuple[int, float]:
        path, mode = item
        score = 0
        if mode == preferred:
            score += 4
        if mode == "default":
            score += 2
        elif mode == "full":
            score += 1
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        return score, mtime

    candidates.sort(key=_rank, reverse=True)
    selected_path, selected_mode = candidates[0]
    return promote_clean_to_canonical(
        book_code,
        language,
        selected_mode or preferred,
        selected_path,
        meta={
            "repromoted": True,
            "repromote_source": str(selected_path),
            **(meta or {}),
        },
    )


def write_translate_run_mode(book_code: str, language: str, run_id: str, mode: str) -> Path:
    run_dir = translate_run_dir(book_code, language, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    mode_path = run_dir / "mode.txt"
    _atomic_write_text(mode_path, normalize_mode(mode) + "\n")
    return mode_path


def write_translate_run_json(
    book_code: str,
    language: str,
    run_id: str,
    filename: str,
    payload: dict[str, Any],
) -> Path:
    run_dir = translate_run_dir(book_code, language, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / filename
    _atomic_write_text(target, json.dumps(_jsonable(payload), ensure_ascii=False, indent=2) + "\n")
    return target


def copy_translate_run_output(
    book_code: str,
    language: str,
    run_id: str,
    source_path: str | Path,
    output_name: str,
) -> Path:
    src = Path(source_path)
    if not src.exists():
        raise FileNotFoundError(src)
    run_dir = translate_run_dir(book_code, language, run_id)
    outputs_dir = run_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    target = outputs_dir / output_name
    shutil.copy2(src, target)
    return target


def write_translate_run_log(
    book_code: str,
    language: str,
    run_id: str,
    filename: str,
    content: str,
) -> Path:
    run_dir = translate_run_dir(book_code, language, run_id)
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    target = logs_dir / filename
    _atomic_write_text(target, (content or "").rstrip("\n") + "\n")
    return target
