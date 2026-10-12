from __future__ import annotations

from pathlib import Path

from gaiden.application.agents.stages import modernize_en_us_2026
from gaiden.application.agents.stages.refine_en_us_2026 import run_refine_en_us_2026


def test_refine_runner_does_not_overwrite_existing_output_when_overwrite_false(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "data/translated/generic_book/en_us/ch_001_chunk_001.txt"
    target = tmp_path / "data/refined/generic_book/en_us/ch_001_chunk_001.txt"
    source.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    source.write_text("The room was quiet, and he looked at the door.", encoding="utf-8")
    target.write_text("existing refined output\n", encoding="utf-8")

    report = run_refine_en_us_2026(
        {
            "job_id": "job_test",
            "book_id": "generic_book",
            "ui_stage": "refine",
            "stage": "refine",
            "language": "en_us",
            "agent_id": "refine_en_us_2026",
            "input": {"source_path": str(source)},
            "output": {"target_path": str(target), "overwrite": False},
        }
    )

    assert report["status"] == "skipped"
    assert target.read_text(encoding="utf-8") == "existing refined output\n"
    assert (
        tmp_path
        / "docs/audit/agent_runs/generic_book/en_us/refine/ch_001_chunk_001.run.json"
    ).exists()


def test_refine_runner_processes_chunk_and_writes_output_and_audit(tmp_path, monkeypatch):
    source = tmp_path / "data/translated/generic_book/en_us/ch_001_chunk_001.txt"
    target = tmp_path / "data/refined/generic_book/en_us/ch_001_chunk_001.txt"
    audit = tmp_path / "docs/audit/agent_runs/generic_book/en_us/refine/ch_001_chunk_001.run.json"
    source.parent.mkdir(parents=True)
    source.write_text("The room was quiet, and he looked at the door.", encoding="utf-8")

    def fake_run_responses(messages, model, temperature=0.2, reasoning_effort="medium"):
        assert model == "gpt-5.4"
        assert temperature == 0.18
        assert messages[0]["role"] == "system"
        assert "Refine EN-US 2026 agent" in messages[0]["content"]
        return {
            "output_text": "The room fell quiet as he looked toward the door.",
            "model": model,
            "usage": {"input_tokens": 10, "output_tokens": 8, "total_tokens": 18},
        }

    monkeypatch.setattr(modernize_en_us_2026.responses_client, "run_responses", fake_run_responses)
    monkeypatch.setattr(modernize_en_us_2026, "_audit_path", lambda job, source_path: Path(audit))

    report = run_refine_en_us_2026(
        {
            "job_id": "job_test",
            "book_id": "generic_book",
            "ui_stage": "refine",
            "stage": "refine",
            "language": "en_us",
            "agent_id": "refine_en_us_2026",
            "input": {"source_path": str(source)},
            "output": {"target_path": str(target), "overwrite": False},
        }
    )

    assert report["status"] == "passed"
    assert report["stage"] == "refine"
    assert report["agent_id"] == "refine_en_us_2026"
    assert target.read_text(encoding="utf-8") == "The room fell quiet as he looked toward the door.\n"
    assert audit.exists()
