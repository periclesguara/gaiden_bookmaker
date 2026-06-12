from __future__ import annotations

from pathlib import Path


def test_new_translate_en_us_runtime_does_not_reference_external_agents_or_assistants():
    runtime_files = [
        Path("gaiden/application/agents/stage_resolver.py"),
        Path("gaiden/application/agents/translate_router.py"),
        Path("gaiden/application/agents/stages/modernize_en_us_2026.py"),
        Path("gaiden/infrastructure/openai/responses_client.py"),
        Path("gaiden/tools/agent_translate_default.py"),
    ]
    forbidden = [
        "ALDEBARAN",
        "ALAMAGUEDERAZ",
        "YODA MING",
        "assistant_id",
        "Assistants API",
        "agent builder",
        "openai assistant",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in runtime_files)

    for token in forbidden:
        assert token not in combined
