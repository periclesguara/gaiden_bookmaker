from __future__ import annotations

from pathlib import Path

from gaiden.application.agents.contracts import load_agent_contract, load_json_contract
from gaiden.application.agents.heading_normalization import normalize_roman_heading_numerals
from gaiden.application.agents.prompt_builder import build_messages
from gaiden.application.agents.stages import modernize_en_us_2026
from gaiden.application.agents.stages.modernize_en_us_2026 import run_modernize_en_us_2026


def test_structural_roman_headings_are_normalized_to_arabic():
    text = "BOOK I\nBOOK X\nCHAPTER IV\nPART III\nSECTION VII\n\nWorld War II remains prose."

    output = normalize_roman_heading_numerals(text)

    assert "BOOK 1" in output
    assert "BOOK 10" in output
    assert "CHAPTER 4" in output
    assert "PART 3" in output
    assert "SECTION 7" in output
    assert "World War II" in output


def test_translate_contract_requires_heading_policy():
    contract = load_json_contract("data/contracts/stages/translate.json")

    assert contract["heading_policy"]["convert_roman_numerals_to_arabic"] is True
    assert contract["heading_policy"]["examples"]["BOOK X"] == "BOOK 10"


def test_prompt_builder_includes_heading_normalization_rule():
    agent_contract = load_agent_contract("modernize_en_us_2026")
    refs = agent_contract["contract_refs"]
    language_contract = load_json_contract(refs["language"])
    stage_contract = load_json_contract(refs["stage"])
    validator_contracts = [load_json_contract(path) for path in refs["validators"]]

    messages = build_messages(
        agent_contract,
        language_contract,
        stage_contract,
        validator_contracts,
        "BOOK IV\n\nText.",
    )

    developer = messages[1]["content"]
    assert "convert_roman_numerals_to_arabic" in developer
    assert "BOOK IV becomes BOOK 4" in developer


def test_modernize_runner_normalizes_headings_before_writing(tmp_path: Path, monkeypatch):
    source = tmp_path / "data/chunks/book_0007/en/ch_001_chunk_001.txt"
    target = tmp_path / "data/translated/book_0007/en_us/ch_001_chunk_001.txt"
    audit = tmp_path / "docs/audit/agent_runs/book_0007/en_us/modernize/ch_001_chunk_001.run.json"
    source.parent.mkdir(parents=True)
    source.write_text("BOOK IV\n\nWorld War II is prose.", encoding="utf-8")

    def fake_run_responses(messages, model, temperature=0.2, reasoning_effort="medium"):
        return {
            "output_text": "BOOK IV\n\nWorld War II is prose.",
            "model": model,
            "usage": {"input_tokens": 10, "output_tokens": 6, "total_tokens": 16},
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
    output = target.read_text(encoding="utf-8")
    assert "BOOK 4" in output
    assert "BOOK IV" not in output
    assert "World War II" in output
