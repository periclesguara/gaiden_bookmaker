from __future__ import annotations

from gaiden.infrastructure import openai_client


class _FakeResponse:
    output_text = "Texte français."


class _FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse()


class _FakeClient:
    def __init__(self):
        self.responses = _FakeResponses()


def test_legacy_agent_facade_uses_the_configured_local_model(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(openai_client, "get_client", lambda: client)
    monkeypatch.setenv("GAIDEN_LOCAL_MODEL", "llama3.1:8b")

    result = openai_client.call_agent_text(
        agent_name="fr_translate_universal_2026",
        text="Text.",
        model="gpt-5.5",
        temperature=0.2,
        system_prompt="Return only French text.",
    )

    assert result == "Texte français."
    assert len(client.responses.calls) == 1
    assert client.responses.calls[0]["model"] == "llama3.1:8b"
