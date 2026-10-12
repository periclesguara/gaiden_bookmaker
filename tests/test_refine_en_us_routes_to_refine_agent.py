from __future__ import annotations

from pathlib import Path

from gaiden.application.agents import refine_router
from gaiden.application.agents.stage_resolver import resolve_agent_for_ui_stage


def test_refine_en_us_resolves_to_refine_agent():
    resolved = resolve_agent_for_ui_stage("refine", "en_us")

    assert resolved["stage"] == "refine"
    assert resolved["language"] == "en_us"
    assert resolved["agent_id"] == "refine_en_us_2026"
    assert resolved["contract_path"] == "data/contracts/agents/refine_en_us_2026.agent.json"


def test_refine_en_us_aliases_normalize_to_en_us():
    assert resolve_agent_for_ui_stage("refine", "en-US")["language"] == "en_us"
    assert resolve_agent_for_ui_stage("refine", "English US")["language"] == "en_us"
    assert resolve_agent_for_ui_stage("refine", "English United States")["language"] == "en_us"
    assert resolve_agent_for_ui_stage("refine", "US English")["language"] == "en_us"


def test_refine_router_calls_refine_runner(tmp_path, monkeypatch):
    source = tmp_path / "data/translated/generic_book/en_us/ch_001_chunk_001.txt"
    target = tmp_path / "data/refined/generic_book/en_us/ch_001_chunk_001.txt"
    source.parent.mkdir(parents=True)
    source.write_text("The room was quiet, and he looked at the door.", encoding="utf-8")
    calls = []

    def fake_run_refine(job):
        calls.append(job)
        target_path = Path(job["output"]["target_path"])
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text("The room fell quiet as he looked toward the door.\n", encoding="utf-8")
        return {
            "status": "passed",
            "validation": {"status": "passed", "validators": []},
            "audit_path": "docs/audit/agent_runs/generic_book/en_us/refine/ch_001_chunk_001.run.json",
        }

    monkeypatch.setattr(refine_router, "run_refine_en_us_2026", fake_run_refine)

    report = refine_router.run_refine(
        book_id="generic_book",
        target_language="English US",
        source_path=source,
        target_path=target,
    )

    assert calls
    assert calls[0]["ui_stage"] == "refine"
    assert calls[0]["stage"] == "refine"
    assert calls[0]["language"] == "en_us"
    assert calls[0]["agent_id"] == "refine_en_us_2026"
    assert report["status"] == "passed"
    assert target.read_text(encoding="utf-8").startswith("The room fell quiet")
