from __future__ import annotations

import json
from pathlib import Path

from gaiden.application.lexical import (
    assemble_stage_user_content,
    build_stage_payload,
    inject_stage_payload,
    load_stage_contract,
    load_stage_rules,
)


def test_load_stage_rules_returns_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIDEN_DATA_ROOT", str(tmp_path))

    assert load_stage_rules("translate", "en") == {}
    assert load_stage_contract("translate", "en") == ""


def test_stage_payload_writes_lexical_memory_with_whole_word_matches(tmp_path, monkeypatch):
    rules_dir = tmp_path / "lexical_rules" / "global" / "en"
    contracts_dir = rules_dir / "contracts"
    rules_dir.mkdir(parents=True)
    contracts_dir.mkdir(parents=True)
    (rules_dir / "translate_hard_replace.json").write_text(
        json.dumps(
            {
                "stage": "translate",
                "rule_type": "hard_replace",
                "language": "en",
                "rules": {"thou": "you", "aught": "anything"},
            }
        ),
        encoding="utf-8",
    )
    (contracts_dir / "translate_contract_en.txt").write_text(
        "Return only the modernized text.",
        encoding="utf-8",
    )
    monkeypatch.setenv("GAIDEN_DATA_ROOT", str(tmp_path))

    payload = build_stage_payload(
        stage="translate",
        book_code="book_9999",
        language="en",
        chunk_id="0001",
        text="Thou taught me nothing, and aught else remains.",
    )

    memory = payload["lexical_memory"]
    assert payload["stage_contract"] == "Return only the modernized text."
    assert payload["text"] == "Thou taught me nothing, and aught else remains."
    assert memory["detected_terms"]["thou"]["count"] == 1
    assert memory["detected_terms"]["aught"]["count"] == 1
    assert "taught" not in memory["detected_terms"]
    memory_path = tmp_path / "translated" / "book_9999" / "en" / "lexical_memory" / "0001.lexical.json"
    assert memory_path.exists()


def test_inject_stage_payload_prepends_system_message(tmp_path, monkeypatch):
    rules_dir = tmp_path / "lexical_rules" / "global" / "en"
    contracts_dir = rules_dir / "contracts"
    rules_dir.mkdir(parents=True)
    contracts_dir.mkdir(parents=True)
    (rules_dir / "refine_soft_replace.json").write_text(
        json.dumps(
            {
                "stage": "refine",
                "rule_type": "soft_replace",
                "language": "en",
                "rules": {"whilst": "while", "by reason of": "because"},
            }
        ),
        encoding="utf-8",
    )
    (contracts_dir / "refine_contract_en.txt").write_text(
        "The best Refine output is faithful modern literary English with the fewest unnecessary changes.",
        encoding="utf-8",
    )
    monkeypatch.setenv("GAIDEN_DATA_ROOT", str(tmp_path))

    messages, payload = inject_stage_payload(
        messages=[{"role": "user", "content": "Text whilst walking."}],
        stage="aldebaran_refine_return",
        book_code="book_9999",
        language="en",
        chunk_id="0002",
        text="Text whilst walking.",
    )

    assert payload["stage"] == "refine"
    assert messages[0]["role"] == "user"
    assert "GLOBAL STAGE CONTRACT:" in messages[0]["content"]
    assert "STAGE RULES:" in messages[0]["content"]
    assert "LEXICAL MEMORY:" in messages[0]["content"]
    assert "INPUT TEXT:" in messages[0]["content"]
    assert "OUTPUT:" in messages[0]["content"]
    assert "by reason of" in messages[0]["content"]
    assert payload["stage_contract"] == (
        "The best Refine output is faithful modern literary English with the fewest unnecessary changes."
    )


def test_stage_message_preserves_existing_system_instruction_order():
    payload = {
        "stage_contract": "Contract text.",
        "stage_rules": {"rules": {"whilst": "while"}},
        "lexical_memory": {"detected_count": 1},
        "text": "Original chunk text.",
    }

    content = assemble_stage_user_content(payload)

    assert content.index("GLOBAL STAGE CONTRACT:") < content.index("STAGE RULES:")
    assert content.index("STAGE RULES:") < content.index("LEXICAL MEMORY:")
    assert content.index("LEXICAL MEMORY:") < content.index("INPUT TEXT:")
    assert content.index("INPUT TEXT:") < content.index("OUTPUT:")
    assert "Original chunk text." in content
