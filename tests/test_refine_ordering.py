from __future__ import annotations

import json
from pathlib import Path

import pytest

from web.pipeline.services import refine_ordering


def _write_split_manifest(root: Path) -> tuple[Path, Path]:
    parts_dir = root / "split_by_chapter" / "parts"
    parts_dir.mkdir(parents=True)
    rows = [
        (1, 1, "chapter_01_part_01.txt", "BOOK I\n\nBook one source."),
        (2, 1, "chapter_02_part_01.txt", "BOOK II\n\nBook two source."),
        (10, 1, "chapter_10_part_01.txt", "BOOK X\n\nBook ten source."),
    ]
    chapters = []
    for chapter_index, part_index, filename, text in rows:
        (parts_dir / filename).write_text(text, encoding="utf-8")
        chapters.append(
            {
                "index": chapter_index,
                "heading": text.splitlines()[0],
                "parts": [
                    {
                        "index": part_index,
                        "filename": filename,
                        "char_count": len(text),
                    }
                ],
            }
        )
    manifest_path = root / "split_by_chapter" / "manifest.json"
    manifest_path.write_text(json.dumps({"chapter_count": len(chapters), "chapters": chapters}), encoding="utf-8")
    return manifest_path, parts_dir


def _chunks(tmp_path: Path, run_id: str = "run_001"):
    manifest_path, parts_dir = _write_split_manifest(tmp_path)
    return refine_ordering.load_refine_chunks_from_manifest(
        manifest_path=manifest_path,
        source_dir=parts_dir,
        book_id="book_0029",
        lang="en_us",
        run_id=run_id,
    )


def _write_output(run_dir: Path, chunk: refine_ordering.RefineChunk, text: str) -> Path:
    output_path = run_dir / chunk.output_filename
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    refine_ordering.write_refine_output_metadata(output_path, chunk, {"status": "passed"})
    return output_path


def test_refine_merge_follows_manifest_order_when_files_arrive_scrambled(tmp_path: Path):
    chunks = _chunks(tmp_path)
    run_dir = tmp_path / "data" / "refined" / "book_0029" / "en_us" / "runs" / "run_001"

    # Deliberately write BOOK X before BOOK II to reproduce filesystem/order-of-arrival drift.
    by_chapter = {chunk.chapter_index: chunk for chunk in chunks}
    _write_output(run_dir, by_chapter[10], "BOOK X\n\nRefined ten.")
    _write_output(run_dir, by_chapter[1], "BOOK I\n\nRefined one.")
    _write_output(run_dir, by_chapter[2], "BOOK II\n\nRefined two.")

    merge_path, manifest = refine_ordering.merge_refine_run_by_manifest(
        run_dir=run_dir,
        chunks=chunks,
        out_path=run_dir / "merge_refine.txt",
        book_code="book_0029",
        language="en",
    )

    assert manifest["status"] == "PASSED"
    merged = merge_path.read_text(encoding="utf-8")
    assert merged.index("BOOK I") < merged.index("BOOK II") < merged.index("BOOK X")
    assert [row["source_chunk_id"] for row in manifest["ordered_outputs"]] == [
        "chapter_01_part_01",
        "chapter_02_part_01",
        "chapter_10_part_01",
    ]
    assert (run_dir / "refine_manifest.json").exists()
    assert (run_dir / "merge_report.json").exists()
    assert manifest["total_expected_chunks"] == 3
    assert manifest["total_merged_chunks"] == 3
    assert [item["merge_position"] for item in manifest["items"]] == [1, 2, 3]


def test_refine_merge_fails_when_expected_chunk_is_missing(tmp_path: Path):
    chunks = _chunks(tmp_path)
    run_dir = tmp_path / "runs" / "run_missing"
    _write_output(run_dir, chunks[0], "BOOK I\n\nRefined one.")
    _write_output(run_dir, chunks[2], "BOOK X\n\nRefined ten.")

    with pytest.raises(ValueError, match="Refine run validation failed"):
        refine_ordering.merge_refine_run_by_manifest(
            run_dir=run_dir,
            chunks=chunks,
            out_path=run_dir / "merge_refine.txt",
        )

    manifest = json.loads((run_dir / "refine_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "FAILED"
    assert manifest["missing_outputs"] == ["chapter_02_part_01"]
    assert (run_dir / "merge_report.json").exists()
    assert not (run_dir / "merge_refine.txt").exists()


def test_refine_merge_fails_when_duplicate_source_chunk_id_exists(tmp_path: Path):
    chunks = _chunks(tmp_path)
    run_dir = tmp_path / "runs" / "run_duplicate"
    for chunk in chunks:
        _write_output(run_dir, chunk, f"BOOK {chunk.chapter_index}\n\nRefined.")

    duplicate = run_dir / "duplicate.refine.txt"
    duplicate.write_text("Duplicate text.", encoding="utf-8")
    dup_meta = refine_ordering.metadata_path_for_output(duplicate)
    payload = json.loads(refine_ordering.metadata_path_for_output(run_dir / chunks[0].output_filename).read_text())
    payload["output_path"] = str(duplicate)
    dup_meta.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Refine run validation failed"):
        refine_ordering.merge_refine_run_by_manifest(
            run_dir=run_dir,
            chunks=chunks,
            out_path=run_dir / "merge_refine.txt",
        )

    manifest = json.loads((run_dir / "refine_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "FAILED"
    assert manifest["duplicates"][0]["source_chunk_id"] == "chapter_01_part_01"
    assert not (run_dir / "merge_refine.txt").exists()


def test_refine_merge_fails_when_output_is_empty(tmp_path: Path):
    chunks = _chunks(tmp_path)
    run_dir = tmp_path / "runs" / "run_empty"
    for chunk in chunks:
        _write_output(run_dir, chunk, "BOOK I\n\nRefined.")
    empty_path = run_dir / chunks[1].output_filename
    empty_path.write_text("\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Refine run validation failed"):
        refine_ordering.merge_refine_run_by_manifest(
            run_dir=run_dir,
            chunks=chunks,
            out_path=run_dir / "merge_refine.txt",
        )

    manifest = json.loads((run_dir / "refine_manifest.json").read_text(encoding="utf-8"))
    assert manifest["empty_outputs"] == [chunks[1].output_filename]
    assert not (run_dir / "merge_refine.txt").exists()


def test_refine_merge_fails_when_metadata_points_to_other_run_book_or_language(tmp_path: Path):
    chunks = _chunks(tmp_path)
    run_dir = tmp_path / "runs" / "run_current"
    for chunk in chunks:
        _write_output(run_dir, chunk, "BOOK I\n\nRefined.")
    meta_path = refine_ordering.metadata_path_for_output(run_dir / chunks[0].output_filename)
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    payload["run_id"] = "run_stale"
    payload["book_id"] = "book_other"
    payload["lang"] = "pt_br"
    meta_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Refine run validation failed"):
        refine_ordering.merge_refine_run_by_manifest(
            run_dir=run_dir,
            chunks=chunks,
            out_path=run_dir / "merge_refine.txt",
        )

    manifest = json.loads((run_dir / "refine_manifest.json").read_text(encoding="utf-8"))
    fields = {item["field"] for item in manifest["metadata_mismatches"]}
    assert {"run_id", "book_id", "lang"} <= fields
    assert not (run_dir / "merge_refine.txt").exists()


def test_refine_merge_fails_when_recorded_output_sha256_diverges(tmp_path: Path):
    chunks = _chunks(tmp_path)
    run_dir = tmp_path / "runs" / "run_sha"
    for chunk in chunks:
        _write_output(run_dir, chunk, "BOOK I\n\nRefined.")
    meta_path = refine_ordering.metadata_path_for_output(run_dir / chunks[0].output_filename)
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    payload["output_sha256"] = "0" * 64
    meta_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Refine run validation failed"):
        refine_ordering.merge_refine_run_by_manifest(
            run_dir=run_dir,
            chunks=chunks,
            out_path=run_dir / "merge_refine.txt",
        )

    manifest = json.loads((run_dir / "refine_manifest.json").read_text(encoding="utf-8"))
    assert any(item.get("kind") == "output" for item in manifest["sha256_mismatches"])
    assert not (run_dir / "merge_refine.txt").exists()


def test_refine_merge_fails_for_extra_file_inside_run_but_ignores_other_run(tmp_path: Path):
    chunks = _chunks(tmp_path)
    run_dir = tmp_path / "runs" / "run_current"
    stale_run_dir = tmp_path / "runs" / "run_stale"
    for chunk in chunks:
        _write_output(run_dir, chunk, f"BOOK {chunk.chapter_index}\n\nRefined.")
    (stale_run_dir / "ch_999_chunk_001.refine.txt").parent.mkdir(parents=True)
    (stale_run_dir / "ch_999_chunk_001.refine.txt").write_text("stale previous run", encoding="utf-8")

    merge_path, manifest = refine_ordering.merge_refine_run_by_manifest(
        run_dir=run_dir,
        chunks=chunks,
        out_path=run_dir / "merge_refine.txt",
    )
    assert manifest["status"] == "PASSED"
    assert merge_path.exists()

    extra = run_dir / "ch_999_chunk_001.refine.txt"
    extra.write_text("extra current run", encoding="utf-8")
    with pytest.raises(ValueError, match="Refine run validation failed"):
        refine_ordering.merge_refine_run_by_manifest(
            run_dir=run_dir,
            chunks=chunks,
            out_path=run_dir / "merge_refine_after_extra.txt",
        )


def test_detect_book_sequence_reports_book_order(tmp_path: Path):
    merge_path = tmp_path / "merge_refine.txt"
    merge_path.write_text("BOOK I\n\nText.\n\nBOOK II\n\nText.\n\nBOOK X\n\nText.\n", encoding="utf-8")

    assert refine_ordering.detect_book_chapter_sequence(merge_path) == ["BOOK I", "BOOK II", "BOOK X"]


def test_refine_merge_infers_book_i_when_source_starts_at_chapter_one_and_later_has_book_ii(tmp_path: Path):
    parts_dir = tmp_path / "split_by_chapter" / "parts"
    parts_dir.mkdir(parents=True)
    (parts_dir / "chapter_01_part_01.txt").write_text("Chapter 1.\n\nOpening.", encoding="utf-8")
    (parts_dir / "chapter_02_part_01.txt").write_text("BOOK II\n\nChapter 1.\n\nSecond book.", encoding="utf-8")
    manifest_path = tmp_path / "split_by_chapter" / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "chapters": [
                    {"index": 1, "parts": [{"index": 1, "filename": "chapter_01_part_01.txt"}]},
                    {"index": 2, "parts": [{"index": 1, "filename": "chapter_02_part_01.txt"}]},
                ]
            }
        ),
        encoding="utf-8",
    )
    chunks = refine_ordering.load_refine_chunks_from_manifest(
        manifest_path=manifest_path,
        source_dir=parts_dir,
        book_id="book_0029",
        lang="en_us",
        run_id="run_infer_book_i",
    )
    run_dir = tmp_path / "runs" / "run_infer_book_i"
    for chunk in chunks:
        _write_output(run_dir, chunk, Path(chunk.source_path).read_text(encoding="utf-8"))

    merge_path, _manifest = refine_ordering.merge_refine_run_by_manifest(
        run_dir=run_dir,
        chunks=chunks,
        out_path=run_dir / "merge_refine.txt",
    )

    assert refine_ordering.detect_book_chapter_sequence(merge_path)[:3] == ["BOOK I", "Chapter 1.", "BOOK II"]
