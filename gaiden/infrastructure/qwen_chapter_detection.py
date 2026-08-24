from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from openai import OpenAI

from gaiden.infrastructure import storage


CONTRACT_PATH = Path("data/contracts/translation/chapter_detection_v1.json")


def _contract() -> dict[str, object]:
    path = storage.repo_root() / CONTRACT_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "gaiden_chapter_detection_contract_v1":
        raise ValueError("Contrato Qwen de detecção de capítulos inválido.")
    return payload


def _structural_lines(text: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        value = line.rstrip("\r\n")
        stripped = value.strip()
        letters = "".join(char for char in stripped if char.isalpha())
        looks_structural = bool(
            stripped
            and len(stripped) <= 300
            and (
                stripped.startswith("#")
                or (letters and letters.upper() == letters)
                or stripped[:1].isdigit()
                or stripped[:1] in "IVXLCDM"
            )
        )
        if looks_structural:
            rows.append({"offset": offset, "line": stripped})
        offset += len(line)
    return rows[:5000]


def detect_chapter_boundaries(text: str, *, client=None) -> dict[str, object]:
    contract = _contract()
    base_url = os.environ.get("GAIDEN_QWEN_BASE_URL", "http://127.0.0.1:8000/v1").strip()
    api_key = os.environ.get("GAIDEN_QWEN_API_KEY", "placeholder").strip() or "placeholder"
    model = os.environ.get("GAIDEN_QWEN_MODEL", str(contract["model_default"])).strip()
    active_client = client or OpenAI(base_url=base_url, api_key=api_key)
    evidence = {
        "source_characters": len(text),
        "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "candidate_lines": _structural_lines(text),
    }
    response = active_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": str(contract["system"])},
            {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)},
        ],
        temperature=0,
        max_tokens=8192,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise RuntimeError("Qwen não retornou sugestões de capítulos.")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Qwen retornou conteúdo que não é JSON estrito.") from exc
    if not isinstance(payload, dict) or payload.get("schema") != contract["response_schema"]:
        raise ValueError("Qwen retornou schema de detecção inesperado.")
    return payload
