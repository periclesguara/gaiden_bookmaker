from __future__ import annotations

from pathlib import Path

from gaiden.application.agents import translate_router


def test_translate_en_us_router_calls_modernize_runner(tmp_path, monkeypatch):
    chunk_dir = tmp_path / "chunks"
    out_dir = tmp_path / "translated" / "book_0007" / "en_us"
    source = chunk_dir / "ch_001_chunk_001.txt"
    chunk_dir.mkdir(parents=True)
    source.write_text("Thou hath spoken.", encoding="utf-8")
    calls = []

    def fake_run_modernize(job):
        calls.append(job)
        target_path = Path(job["output"]["target_path"])
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text("You have spoken.\n", encoding="utf-8")
        return {
            "status": "passed",
            "validation": {"status": "passed", "validators": []},
            "audit_path": "docs/audit/agent_runs/book_0007/en_us/modernize/ch_001_chunk_001.run.json",
        }

    monkeypatch.setattr(translate_router, "run_modernize_en_us_2026", fake_run_modernize)

    report = translate_router.run_translate_en_us_modernize(
        book_id="book_0007",
        chunk_dir=chunk_dir,
        out_dir=out_dir,
    )

    assert calls
    assert calls[0]["ui_stage"] == "translate"
    assert calls[0]["stage"] == "modernize"
    assert calls[0]["language"] == "en_us"
    assert calls[0]["agent_id"] == "modernize_en_us_2026"
    assert report["resolved_stage"] == "modernize"
    assert report["agent_id"] == "modernize_en_us_2026"
    output = (out_dir / "ch_001_chunk_001.txt").read_text(encoding="utf-8")
    assert "Here is" not in output
    assert "Certainly" not in output
    assert "Thou" not in output
    assert "hath" not in output
