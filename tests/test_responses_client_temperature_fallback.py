from __future__ import annotations

from gaiden.infrastructure.openai import responses_client


class _FakeResponse:
    output_text = "Modernized text."
    usage = {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}


class _FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if "temperature" in kwargs:
            raise RuntimeError("Unsupported parameter: 'temperature' is not supported with this model.")
        return _FakeResponse()


class _FakeClient:
    def __init__(self):
        self.responses = _FakeResponses()


def test_run_responses_retries_without_temperature_when_model_rejects_it(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(responses_client, "get_client", lambda: client)

    result = responses_client.run_responses(
        [{"role": "user", "content": "Text"}],
        model="gpt-5.4",
        temperature=0.2,
        reasoning_effort="medium",
    )

    assert result["output_text"] == "Modernized text."
    assert len(client.responses.calls) == 2
    assert "temperature" in client.responses.calls[0]
    assert "temperature" not in client.responses.calls[1]
