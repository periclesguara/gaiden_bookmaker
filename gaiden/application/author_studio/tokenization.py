from __future__ import annotations

from dataclasses import dataclass


DEFAULT_ENCODING = "cl100k_base"
DEFAULT_TOKENIZER_NAME = f"tiktoken:{DEFAULT_ENCODING}"
FALLBACK_TOKENIZER_NAME = "character-estimate:v1"


class TokenizerUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenCount:
    count: int
    tokenizer_name: str
    normalized_language: str
    used_fallback: bool = False


def normalize_language(language: str | None) -> str:
    normalized = (language or "").strip().lower()
    aliases = {
        "eng": "en",
        "english": "en",
        "por": "pt",
        "portuguese": "pt",
        "português": "pt",
    }
    return aliases.get(normalized, normalized or "und")


def _encoding():
    try:
        import tiktoken
    except ImportError as exc:  # pragma: no cover - covered through the explicit fallback path
        raise TokenizerUnavailableError(
            "tiktoken não está instalado; instale as dependências declaradas do projeto."
        ) from exc
    return tiktoken.get_encoding(DEFAULT_ENCODING)


def _character_estimate(text: str, normalized_language: str) -> int:
    divisor = 4.0 if normalized_language == "en" else 3.6
    return max(1, int(len(text) / divisor)) if text else 0


def count_tokens(
    text: str,
    language: str | None = None,
    *,
    allow_character_fallback: bool = False,
) -> TokenCount:
    normalized_language = normalize_language(language)
    try:
        count = len(_encoding().encode(text, disallowed_special=()))
    except TokenizerUnavailableError:
        if not allow_character_fallback:
            raise
        return TokenCount(
            count=_character_estimate(text, normalized_language),
            tokenizer_name=FALLBACK_TOKENIZER_NAME,
            normalized_language=normalized_language,
            used_fallback=True,
        )
    return TokenCount(
        count=count,
        tokenizer_name=DEFAULT_TOKENIZER_NAME,
        normalized_language=normalized_language,
    )


def split_by_token_limit(text: str, maximum_tokens: int) -> list[str]:
    if maximum_tokens < 1:
        raise ValueError("maximum_tokens deve ser positivo.")
    encoding = _encoding()
    tokens = encoding.encode(text, disallowed_special=())
    if not tokens:
        return []
    return [
        encoding.decode(tokens[offset : offset + maximum_tokens])
        for offset in range(0, len(tokens), maximum_tokens)
    ]
