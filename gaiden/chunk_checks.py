from __future__ import annotations

from pathlib import Path
from typing import Any

from gaiden.chunk_manifest import sha256_file


def run_checks(
    *,
    normalized_path: Path,
    normalized_text: str,
    normalized_lines: list[str],
    manifest: dict[str, Any],
    max_tokens: int,
) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []

    if not normalized_path.exists():
        failures.append("normalized_missing")
        return {"check_ok": False, "failures": failures, "warnings": warnings}

    if len("".join(normalized_lines).strip()) == 0:
        failures.append("normalized_empty")

    manifest_sha = manifest.get("normalized_sha256")
    if not manifest_sha:
        failures.append("manifest_missing_normalized_sha256")
    else:
        current_sha = sha256_file(normalized_path)
        if current_sha != manifest_sha:
            failures.append("normalized_sha256_mismatch")

    chapters = manifest.get("chapters") or []
    if not chapters:
        failures.append("no_chapters_in_manifest")

    normalized_chars = len(normalized_text)
    covered_chars = 0
    for chapter in chapters:
        for chunk in chapter.get("chunks", []):
            covered_chars += int(chunk.get("char_count") or 0)

    tolerance_ratio = 0.01
    tolerance_chars = max(int(normalized_chars * tolerance_ratio), 1000)
    coverage_chars_ok = covered_chars >= max(0, normalized_chars - tolerance_chars)
    if not coverage_chars_ok:
        failures.append("coverage_chars_below_tolerance")

    # Coverage by line indices (0-based inclusive).
    covered: set[int] = set()
    for chapter in chapters:
        for chunk in chapter.get("chunks", []):
            start = chunk.get("start_line_idx")
            end = chunk.get("end_line_idx")
            if start is None or end is None:
                failures.append("chunk_missing_line_range")
                continue
            if start > end:
                failures.append("chunk_invalid_line_range")
                continue
            for i in range(start, end + 1):
                covered.add(i)

    total_lines = len(normalized_lines)
    if total_lines > 0:
        missing = [i for i in range(0, total_lines) if i not in covered]
        if missing:
            failures.append("coverage_missing_lines")

    # Token limits and cross-chapter range validation.
    for chapter in chapters:
        ch_id = chapter.get("chapter_id")
        ch_start = chapter.get("start_line_idx")
        ch_end = chapter.get("end_line_idx")
        for chunk in chapter.get("chunks", []):
            tok = chunk.get("token_estimate")
            if tok is not None and tok > max_tokens:
                failures.append("chunk_exceeds_max_tokens")
                break
            if chunk.get("chapter_id") != ch_id:
                failures.append("chunk_chapter_id_mismatch")
                break
            start = chunk.get("start_line_idx")
            end = chunk.get("end_line_idx")
            if start is None or end is None:
                continue
            if ch_start is not None and start < ch_start:
                failures.append("chunk_cross_chapter_start")
                break
            if ch_end is not None and end > ch_end:
                failures.append("chunk_cross_chapter_end")
                break

    estimator = manifest.get("config", {}).get("estimator")
    if estimator == "chars4":
        warnings.append("token_estimator_chars4")

    return {
        "check_ok": len(failures) == 0,
        "failures": sorted(set(failures)),
        "warnings": sorted(set(warnings)),
        "coverage_chars_ok": coverage_chars_ok,
        "normalized_chars": normalized_chars,
        "covered_chars": covered_chars,
        "coverage_chars_tolerance": tolerance_chars,
    }
