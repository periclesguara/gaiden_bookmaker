from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

PARTICLES = {"a", "an", "the", "da", "das", "de", "do", "dos", "e", "of", "von", "van"}
INITIAL_ARTICLES = {"a", "an", "the", "o", "os", "a", "as", "le", "la", "les", "el", "los", "las"}


def ascii_words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.findall(r"[A-Za-z0-9]+", ascii_value)


def canonicalize(value: str) -> str:
    return " ".join(word.lower() for word in ascii_words(value))


def _ensure_minimum(value: str, words: list[str], length: int = 3) -> str:
    pool = "".join(word[1:] for word in reversed(words)).upper() + "".join(words).upper()
    for char in pool:
        if len(value) >= length:
            break
        value += char
    return (value + "XXX")[: max(length, len(value))]


def author_code_base(name: str, particles: set[str] | None = None) -> str:
    words = ascii_words(name)
    ignored = PARTICLES if particles is None else particles
    significant = [word for word in words if word.lower() not in ignored] or words
    if not significant:
        return "AUT"
    if len(significant) == 1:
        return (significant[0].upper() + "XXX")[:3]
    base = "".join(word[0] for word in significant).upper()
    return _ensure_minimum(base, significant, 3)


def work_code_base(title: str) -> str:
    words = ascii_words(title)
    while len(words) > 1 and words[0].lower() in INITIAL_ARTICLES:
        words.pop(0)
    if not words:
        return "WORKX"
    return (words[0].upper() + "XXXXX")[:5]


def unique_code(base: str, exists: Callable[[str], bool], max_length: int) -> str:
    candidate = base[:max_length]
    if not exists(candidate):
        return candidate
    sequence = 2
    while True:
        suffix = str(sequence)
        candidate = f"{base[: max_length - len(suffix)]}{suffix}"
        if not exists(candidate):
            return candidate
        sequence += 1


def generate_author_code(name: str, exists: Callable[[str], bool]) -> str:
    return unique_code(author_code_base(name), exists, 12)


def generate_work_code(author_code: str, title: str, exists: Callable[[str], bool]) -> str:
    prefix = f"{author_code}-"
    max_suffix = min(5, 32 - len(prefix))
    suffix = unique_code(work_code_base(title), lambda value: exists(prefix + value), max_suffix)
    return prefix + suffix


def generate_source_code(work_code: str, sequence: int) -> str:
    return f"{work_code}-SRC{sequence:03d}"


def generate_canonical_code(work_code: str) -> str:
    return f"{work_code}-CAN001"
