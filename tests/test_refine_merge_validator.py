from __future__ import annotations

from gaiden.application.pipeline.merge_validator import validate_manifest_driven_outputs


def _expected():
    return [
        {"source_chunk_id": "book_0030_en_us_ch001_chunk001", "chapter_index": 1, "chunk_index": 1},
        {"source_chunk_id": "book_0030_en_us_ch001_chunk002", "chapter_index": 1, "chunk_index": 2},
    ]


def _received():
    return [
        {
            "source_chunk_id": "book_0030_en_us_ch001_chunk001",
            "book_code": "book_0030",
            "language": "en_us",
            "stage": "refine",
            "chapter_index": 1,
            "chunk_index": 1,
            "path": "a.txt",
        },
        {
            "source_chunk_id": "book_0030_en_us_ch001_chunk002",
            "book_code": "book_0030",
            "language": "en_us",
            "stage": "refine",
            "chapter_index": 1,
            "chunk_index": 2,
            "path": "b.txt",
        },
    ]


def test_merge_validator_passes_manifest_ordered_outputs():
    report = validate_manifest_driven_outputs(
        expected=_expected(),
        received=_received(),
        book_code="book_0030",
        language="en_us",
        stage="refine",
    )

    assert report["ok"]


def test_merge_validator_detects_missing_chunk():
    report = validate_manifest_driven_outputs(
        expected=_expected(),
        received=_received()[:1],
        book_code="book_0030",
        language="en_us",
        stage="refine",
    )

    assert not report["ok"]
    assert any(error["type"] == "MISSING_CHUNK" for error in report["errors"])


def test_merge_validator_detects_duplicate_chunk():
    rows = _received()
    report = validate_manifest_driven_outputs(
        expected=_expected(),
        received=[rows[0], rows[0], rows[1]],
        book_code="book_0030",
        language="en_us",
        stage="refine",
    )

    assert not report["ok"]
    assert any(error["type"] == "DUPLICATE_CHUNK" for error in report["errors"])


def test_merge_validator_detects_extra_chunk():
    rows = _received()
    rows.append({**rows[-1], "source_chunk_id": "extra"})

    report = validate_manifest_driven_outputs(
        expected=_expected(),
        received=rows,
        book_code="book_0030",
        language="en_us",
        stage="refine",
    )

    assert not report["ok"]
    assert any(error["type"] == "EXTRA_CHUNK" for error in report["errors"])


def test_merge_validator_detects_out_of_manifest_order():
    report = validate_manifest_driven_outputs(
        expected=_expected(),
        received=list(reversed(_received())),
        book_code="book_0030",
        language="en_us",
        stage="refine",
    )

    assert not report["ok"]
    assert any(error["type"] == "FILES_OUT_OF_MANIFEST_ORDER" for error in report["errors"])
