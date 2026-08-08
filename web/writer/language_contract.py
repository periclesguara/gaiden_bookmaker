from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError

from gaiden.writer_engine.language_contract import (
    apply_deterministic_rules,
    canonical_contract_json,
    contract_prompt,
    contract_sha256,
    default_language_contract,
    generated_text_violations,
    language_contract_for,
    validate_language_contract as validate_engine_language_contract,
)


def validate_language_contract(contract: Any) -> None:
    try:
        validate_engine_language_contract(contract)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


__all__ = [
    "apply_deterministic_rules",
    "canonical_contract_json",
    "contract_prompt",
    "contract_sha256",
    "default_language_contract",
    "generated_text_violations",
    "language_contract_for",
    "validate_language_contract",
]
