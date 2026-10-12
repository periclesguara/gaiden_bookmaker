from __future__ import annotations

import pytest

from gaiden.application.pipeline.fail_closed_merge import validate_repair_and_write


def test_pipeline_does_not_overwrite_canonical_when_boundary_validation_fails(tmp_path):
    canonical = tmp_path / "data" / "canonical" / "book_0030" / "en_us" / "merge_refine_en_us.txt"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("previous valid canonical\n", encoding="utf-8")

    with pytest.raises(ValueError):
        validate_repair_and_write(
            text="Then their bodies are in their prime, and they will also cease\n\nAmbiguous continuation",
            out_path=canonical,
            root=tmp_path,
            book_code="book_0030",
            language="en_us",
            stage="refine",
            run_id="run_failed",
            merge_validation={"ok": True},
            chunk_order_report={"ok": True},
            allow_auto_repair=False,
        )

    assert canonical.read_text(encoding="utf-8") == "previous valid canonical\n"
    failed_dir = tmp_path / "data" / "failed_merges" / "book_0030" / "en_us" / "refine" / "run_failed"
    assert (failed_dir / "failed_merge_diagnostics.json").exists()
    assert (failed_dir / "boundary_validation_report.json").exists()
