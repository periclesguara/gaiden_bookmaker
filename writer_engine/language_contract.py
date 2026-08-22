from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

_SCHEMA_VERSION = 1
_ALLOWED_OPERATIONS = {"modernize", "translate_and_modernize", "original"}
_ALLOWED_ARCHAISM_LEVELS = {"none", "light", "moderate", "strong"}
_ALLOWED_FLUENCY_LEVELS = {"literal", "natural", "literary"}
_MAX_TERMS = 250
_MAX_TERM_LENGTH = 160

_DEFAULT_CONTRACT: dict[str, Any] = {
    "schema_version": _SCHEMA_VERSION,
    "source_language": "en-GB",
    "target_language": "en-US",
    "target_variant": "Contemporary American English",
    "operation": "original",
    "preserve": [
        "semantic_meaning",
        "proper_names",
        "characters",
        "plot_facts",
        "chronology",
        "causal_logic",
        "point_of_view",
        "dialogue_intent",
    ],
    "reference_policy": {
        "semantic_content_only": True,
        "preserve_source_wording": False,
        "imitate_source_style": False,
        "preserve_victorianism": False,
    },
    "deleted_terms": [],
    "forbidden_terms": [
        "thou",
        "thee",
        "thy",
        "thine",
        "hath",
        "doth",
    ],
    "replacements": {},
    "style": {
        "reduce_archaisms": "strong",
        "fluency": "natural",
        "avoid_repetition": True,
        "preserve_authorial_voice": False,
        "american_english_only": True,
        "remove_obsolete_connectors": True,
        "avoid_long_sentences": True,
        "max_sentence_words": 32,
    },
    "constraints": {
        "no_summary": True,
        "no_commentary": True,
        "no_new_facts": True,
    },
    "validation": {
        "reject_forbidden_terms": True,
        "max_word_variation_percent": 20,
        "retry_attempts": 1,
    },
}

_REQUIRED_KEYS = set(_DEFAULT_CONTRACT)
_REQUIRED_REFERENCE_POLICY_KEYS = set(_DEFAULT_CONTRACT["reference_policy"])
_REQUIRED_STYLE_KEYS = set(_DEFAULT_CONTRACT["style"])
_REQUIRED_CONSTRAINT_KEYS = set(_DEFAULT_CONTRACT["constraints"])
_REQUIRED_VALIDATION_KEYS = set(_DEFAULT_CONTRACT["validation"])


SUPPORTED_OUTPUT_LANGUAGES = ("en-US", "en-GB", "pt-BR")


def language_contract_for(output_language: str) -> dict[str, Any]:
    if output_language not in SUPPORTED_OUTPUT_LANGUAGES:
        raise ValueError(
            "Idioma de saída não suportado. Use en-US, en-GB ou pt-BR."
        )

    contract = deepcopy(_DEFAULT_CONTRACT)
    if output_language == "en-GB":
        contract["target_language"] = "en-GB"
        contract["target_variant"] = "Contemporary British English"
        contract["style"]["american_english_only"] = False
    elif output_language == "pt-BR":
        contract["source_language"] = "pt-BR"
        contract["target_language"] = "pt-BR"
        contract["target_variant"] = "Português brasileiro contemporâneo"
        contract["operation"] = "original"
        contract["style"]["american_english_only"] = False
    return contract


def default_language_contract() -> dict[str, Any]:
    return language_contract_for("en-US")


def _error(message: str) -> None:
    raise ValueError(message)


def _validate_string(value: Any, label: str, *, maximum: int = 160) -> str:
    if not isinstance(value, str) or not value.strip():
        _error(f"{label} deve ser um texto não vazio.")
    value = value.strip()
    if len(value) > maximum:
        _error(f"{label} excede {maximum} caracteres.")
    return value


def _validate_exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _error(f"{label} deve ser um objeto JSON.")
    missing = expected - set(value)
    unknown = set(value) - expected
    if missing:
        _error(f"{label}: campos obrigatórios ausentes: {', '.join(sorted(missing))}.")
    if unknown:
        _error(f"{label}: campos desconhecidos: {', '.join(sorted(unknown))}.")
    return value


def _validate_term_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        _error(f"{label} deve ser uma lista JSON.")
    if len(value) > _MAX_TERMS:
        _error(f"{label} aceita no máximo {_MAX_TERMS} itens.")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        term = _validate_string(item, label, maximum=_MAX_TERM_LENGTH)
        folded = term.casefold()
        if folded in seen:
            _error(f"{label} contém item duplicado: {term}.")
        seen.add(folded)
        normalized.append(term)
    return normalized


def validate_language_contract(contract: Any) -> None:
    contract = _validate_exact_keys(contract, _REQUIRED_KEYS, "Contrato de linguagem")
    if isinstance(contract["schema_version"], bool) or contract["schema_version"] != _SCHEMA_VERSION:
        _error(f"schema_version deve ser {_SCHEMA_VERSION}.")

    _validate_string(contract["source_language"], "source_language", maximum=40)
    target_language = _validate_string(
        contract["target_language"], "target_language", maximum=40
    )
    _validate_string(contract["target_variant"], "target_variant")
    if target_language not in SUPPORTED_OUTPUT_LANGUAGES:
        _error("target_language deve ser en-US, en-GB ou pt-BR.")

    operation = _validate_string(contract["operation"], "operation", maximum=40)
    if operation not in _ALLOWED_OPERATIONS:
        _error("operation deve ser modernize, translate_and_modernize ou original.")

    preserve = _validate_term_list(contract["preserve"], "preserve")
    if not preserve:
        _error("preserve deve declarar ao menos uma característica a conservar.")

    reference_policy = _validate_exact_keys(
        contract["reference_policy"],
        _REQUIRED_REFERENCE_POLICY_KEYS,
        "reference_policy",
    )
    for key, value in reference_policy.items():
        if not isinstance(value, bool):
            _error(f"reference_policy.{key} deve ser booleano.")

    deleted = _validate_term_list(contract["deleted_terms"], "deleted_terms")
    forbidden = _validate_term_list(contract["forbidden_terms"], "forbidden_terms")

    replacements = contract["replacements"]
    if not isinstance(replacements, dict):
        _error("replacements deve ser um objeto JSON de termo original para termo substituto.")
    if len(replacements) > _MAX_TERMS:
        _error(f"replacements aceita no máximo {_MAX_TERMS} itens.")
    replacement_sources: set[str] = set()
    for source, target in replacements.items():
        source = _validate_string(source, "replacements (origem)", maximum=_MAX_TERM_LENGTH)
        _validate_string(target, f"replacements[{source}]", maximum=_MAX_TERM_LENGTH)
        folded_source = source.casefold()
        if folded_source in replacement_sources:
            _error(f"replacements contém origem duplicada: {source}.")
        replacement_sources.add(folded_source)

    deleted_folded = {term.casefold() for term in deleted}
    forbidden_folded = {term.casefold() for term in forbidden}
    if deleted_folded & replacement_sources:
        _error("Um termo não pode estar simultaneamente em deleted_terms e replacements.")
    replacement_targets = {
        str(value).casefold() for value in replacements.values()
    }
    if forbidden_folded & replacement_targets:
        _error("Um texto substituto não pode estar em forbidden_terms.")
    if deleted_folded & replacement_targets:
        _error("Um texto substituto não pode estar em deleted_terms.")
    if replacement_sources & replacement_targets:
        _error("Substituições encadeadas ou circulares não são permitidas.")

    style = _validate_exact_keys(contract["style"], _REQUIRED_STYLE_KEYS, "style")
    archaism_level = _validate_string(
        style["reduce_archaisms"], "style.reduce_archaisms", maximum=20
    )
    if archaism_level not in _ALLOWED_ARCHAISM_LEVELS:
        _error("style.reduce_archaisms deve ser none, light, moderate ou strong.")
    fluency = _validate_string(style["fluency"], "style.fluency", maximum=20)
    if fluency not in _ALLOWED_FLUENCY_LEVELS:
        _error("style.fluency deve ser literal, natural ou literary.")
    for key in (
        "avoid_repetition",
        "preserve_authorial_voice",
        "american_english_only",
        "remove_obsolete_connectors",
        "avoid_long_sentences",
    ):
        if not isinstance(style[key], bool):
            _error(f"style.{key} deve ser booleano.")
    max_sentence_words = style["max_sentence_words"]
    if (
        isinstance(max_sentence_words, bool)
        or not isinstance(max_sentence_words, int)
        or not 12 <= max_sentence_words <= 60
    ):
        _error("style.max_sentence_words deve ser um inteiro entre 12 e 60.")

    constraints = _validate_exact_keys(
        contract["constraints"], _REQUIRED_CONSTRAINT_KEYS, "constraints"
    )
    for key, value in constraints.items():
        if not isinstance(value, bool):
            _error(f"constraints.{key} deve ser booleano.")

    validation = _validate_exact_keys(
        contract["validation"], _REQUIRED_VALIDATION_KEYS, "validation"
    )
    if not isinstance(validation["reject_forbidden_terms"], bool):
        _error("validation.reject_forbidden_terms deve ser booleano.")
    retries = validation["retry_attempts"]
    if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 3:
        _error("validation.retry_attempts deve ser um inteiro entre 0 e 3.")
    variation = validation["max_word_variation_percent"]
    if isinstance(variation, bool) or not isinstance(variation, int) or not 0 <= variation <= 100:
        _error("validation.max_word_variation_percent deve ser um inteiro entre 0 e 100.")


def canonical_contract_json(contract: dict[str, Any]) -> str:
    validate_language_contract(contract)
    return json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def contract_sha256(contract: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_contract_json(contract).encode("utf-8")).hexdigest()


def contract_prompt(contract: dict[str, Any]) -> str:
    validate_language_contract(contract)
    return json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2)


def _term_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term)
    prefix = r"(?<!\w)" if term[0].isalnum() or term[0] == "_" else ""
    suffix = r"(?!\w)" if term[-1].isalnum() or term[-1] == "_" else ""
    return re.compile(prefix + escaped + suffix, re.IGNORECASE | re.UNICODE)


def _match_case(replacement: str, matched: str) -> str:
    if matched.isupper():
        return replacement.upper()
    if matched[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def apply_deterministic_rules(text: str, contract: dict[str, Any]) -> str:
    validate_language_contract(contract)
    result = text
    replacements = sorted(
        contract["replacements"].items(), key=lambda item: len(item[0]), reverse=True
    )
    for source, target in replacements:
        result = _term_pattern(source).sub(
            lambda match, target=target: _match_case(target, match.group(0)), result
        )
    for term in sorted(contract["deleted_terms"], key=len, reverse=True):
        result = _term_pattern(term).sub("", result)
    result = re.sub(r"[ \t]+([,.;:!?])", r"\1", result)
    result = re.sub(r"[ \t]{2,}", " ", result)
    return result.strip()


def generated_text_violations(
    text: str, contract: dict[str, Any], *, target_words: int
) -> list[str]:
    validate_language_contract(contract)
    violations: list[str] = []
    if contract["validation"]["reject_forbidden_terms"]:
        forbidden = [
            term for term in contract["forbidden_terms"] if _term_pattern(term).search(text)
        ]
        if forbidden:
            violations.append(
                "termos proibidos ainda presentes: " + ", ".join(forbidden[:10])
            )
    leftovers = [
        term for term in contract["deleted_terms"] if _term_pattern(term).search(text)
    ]
    leftovers.extend(
        term for term in contract["replacements"] if _term_pattern(term).search(text)
    )
    if leftovers:
        violations.append(
            "regras determinísticas não aplicadas: " + ", ".join(leftovers[:10])
        )

    if contract["style"]["avoid_long_sentences"]:
        maximum_sentence_words = contract["style"]["max_sentence_words"]
        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", text)
            if sentence.strip()
        ]
        longest_sentence = max(
            (
                len(re.findall(r"\b[\w’'-]+\b", sentence, re.UNICODE))
                for sentence in sentences
            ),
            default=0,
        )
        if longest_sentence > maximum_sentence_words:
            violations.append(
                "frase excessivamente longa: "
                f"{longest_sentence} palavras; máximo {maximum_sentence_words}"
            )

    actual_words = len(text.split())
    allowed_percent = contract["validation"]["max_word_variation_percent"]
    minimum = round(target_words * (1 - allowed_percent / 100))
    maximum = round(target_words * (1 + allowed_percent / 100))
    if not minimum <= actual_words <= maximum:
        violations.append(
            f"tamanho fora da faixa contratada: {actual_words} palavras; esperado {minimum}–{maximum}"
        )
    return violations
