from __future__ import annotations

from pathlib import Path

from gaiden.application.agents.stages import modernize_en_us_2026
from gaiden.application.agents.stages.modernize_en_us_2026 import run_modernize_en_us_2026


def test_runner_does_not_overwrite_existing_output_when_overwrite_false(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "data/chunks/book_0007/en/ch_001_chunk_001.txt"
    target = tmp_path / "data/translated/book_0007/en_us/ch_001_chunk_001.txt"
    source.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    source.write_text("Thou hast done this.", encoding="utf-8")
    target.write_text("existing output\n", encoding="utf-8")

    report = run_modernize_en_us_2026(
        {
            "job_id": "job_test",
            "book_id": "book_0007",
            "stage": "modernize",
            "language": "en_us",
            "agent_id": "modernize_en_us_2026",
            "input": {"source_path": str(source)},
            "output": {"target_path": str(target), "overwrite": False},
        }
    )

    assert report["status"] == "skipped"
    assert target.read_text(encoding="utf-8") == "existing output\n"
    assert (
        tmp_path
        / "docs/audit/agent_runs/book_0007/en_us/modernize/ch_001_chunk_001.run.json"
    ).exists()


def test_runner_processes_chunk_and_writes_output_and_audit(tmp_path, monkeypatch):
    source = tmp_path / "data/chunks/book_0007/en/ch_001_chunk_001.txt"
    target = tmp_path / "data/translated/book_0007/en_us/ch_001_chunk_001.txt"
    audit = tmp_path / "docs/audit/agent_runs/book_0007/en_us/modernize/ch_001_chunk_001.run.json"
    source.parent.mkdir(parents=True)
    source.write_text("Thou hast done this.", encoding="utf-8")

    def fake_run_responses(messages, model, temperature=0.2, reasoning_effort="medium"):
        assert model == "gpt-5.4"
        assert messages[0]["role"] == "system"
        return {
            "output_text": "You have done this.",
            "model": model,
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr(modernize_en_us_2026.responses_client, "run_responses", fake_run_responses)
    monkeypatch.setattr(modernize_en_us_2026, "_audit_path", lambda job, source_path: Path(audit))

    report = run_modernize_en_us_2026(
        {
            "job_id": "job_test",
            "book_id": "book_0007",
            "stage": "modernize",
            "language": "en_us",
            "agent_id": "modernize_en_us_2026",
            "input": {"source_path": str(source)},
            "output": {"target_path": str(target), "overwrite": False},
        }
    )

    assert report["status"] == "passed"
    assert target.read_text(encoding="utf-8") == "You have done this.\n"
    assert audit.exists()
