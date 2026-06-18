from __future__ import annotations

import json
from pathlib import Path


FORBIDDEN_EXTERNAL_RUNTIME_TOKENS = [
    "ALDEBARAN",
    "ALAMAGUEDERAZ",
    "YODA MING",
    "assistant_id",
    "Assistants API",
    "agent builder",
    "openai assistant",
    "beta.assistants",
    "beta.threads",
    "client.beta",
    "threads.create",
    "runs.create",
]


def _read(paths: list[Path]) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def test_translate_en_us_runtime_does_not_reference_external_agents_or_assistants():
    runtime_files = [
        Path("gaiden/application/agents/stage_resolver.py"),
        Path("gaiden/application/agents/translate_router.py"),
        Path("gaiden/application/agents/stages/modernize_en_us_2026.py"),
        Path("gaiden/infrastructure/openai/responses_client.py"),
        Path("gaiden/tools/agent_translate_default.py"),
    ]
    combined = _read(runtime_files)

    for token in FORBIDDEN_EXTERNAL_RUNTIME_TOKENS:
        assert token not in combined


def test_refine_en_us_runtime_does_not_reference_external_agents_or_assistants():
    runtime_files = [
        Path("gaiden/application/agents/stage_resolver.py"),
        Path("gaiden/application/agents/refine_router.py"),
        Path("gaiden/application/agents/stages/refine_en_us_2026.py"),
        Path("gaiden/application/agents/stages/modernize_en_us_2026.py"),
        Path("gaiden/infrastructure/openai/responses_client.py"),
    ]
    combined = _read(runtime_files)

    for token in FORBIDDEN_EXTERNAL_RUNTIME_TOKENS:
        assert token not in combined


def test_agent_registry_is_source_for_internal_agent_resolution():
    registry = json.loads(Path("data/contracts/agent_registry.json").read_text(encoding="utf-8"))

    agent_ids = {agent["id"] for agent in registry["agents"]}
    assert "modernize_en_us_2026" in agent_ids
    assert "refine_en_us_2026" in agent_ids
    assert all("assistant_id" not in agent for agent in registry["agents"])


def test_stage_resolver_maps_ui_stages_to_internal_agents():
    from gaiden.application.agents.stage_resolver import resolve_agent_for_ui_stage

    translate = resolve_agent_for_ui_stage("translate", "en_us")
    refine = resolve_agent_for_ui_stage("refine", "en_us")

    assert translate["stage"] == "modernize"
    assert translate["language"] == "en_us"
    assert translate["agent_id"] == "modernize_en_us_2026"
    assert refine["stage"] == "refine"
    assert refine["language"] == "en_us"
    assert refine["agent_id"] == "refine_en_us_2026"


def test_new_routers_do_not_call_hosted_assistants_api():
    router_files = [
        Path("gaiden/application/agents/translate_router.py"),
        Path("gaiden/application/agents/refine_router.py"),
        Path("gaiden/application/agents/stages/modernize_en_us_2026.py"),
        Path("gaiden/application/agents/stages/refine_en_us_2026.py"),
    ]
    combined = _read(router_files)

    forbidden_api_calls = [
        "beta.assistants",
        "beta.threads",
        "client.beta",
        "threads.create",
        "runs.create",
        "assistant_id",
    ]
    for token in forbidden_api_calls:
        assert token not in combined


def test_en_us_polish_options_expose_only_internal_agent():
    views_source = Path("web/pipeline/views.py").read_text(encoding="utf-8")
    assert 'POLISH_AGENT_OPTIONS = (\n    "polish_en_us_aristotle_2026",\n)' in views_source

    forbidden_options = ["English Polidor", "Alamaguederaz", "Aldebaran", "Bismarck", "Kaiser"]
    polish_options_block = views_source.split("POLISH_AGENT_OPTIONS = (", 1)[1].split(")", 1)[0]
    for option in forbidden_options:
        assert option not in polish_options_block


def test_markitdown_is_project_dependency_for_normalize():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "markitdown[all]>=0.1.5" in pyproject
