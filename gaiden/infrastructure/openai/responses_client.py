from __future__ import annotations

from typing import Any

from gaiden.openai_client import get_client


def _extract_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text).strip()
    try:
        return str(response.output[0].content[0].text).strip()
    except Exception as exc:
        raise RuntimeError("OpenAI Responses API returned no output text.") from exc


def _usage_dict(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    if isinstance(usage, dict):
        return {
            "input_tokens": usage.get("input_tokens") or usage.get("prompt_tokens"),
            "output_tokens": usage.get("output_tokens") or usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
    return {
        "input_tokens": getattr(usage, "input_tokens", getattr(usage, "prompt_tokens", None)),
        "output_tokens": getattr(usage, "output_tokens", getattr(usage, "completion_tokens", None)),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def run_responses(
    messages: list[dict[str, str]],
    model: str,
    temperature: float = 0.2,
    reasoning_effort: str = "medium",
) -> dict[str, Any]:
    client = get_client()
    response = client.responses.create(
        model=model,
        input=messages,
        temperature=temperature,
        reasoning={"effort": reasoning_effort},
    )
    return {
        "output_text": _extract_output_text(response),
        "model": model,
        "usage": _usage_dict(response),
        "raw_response": response,
    }
