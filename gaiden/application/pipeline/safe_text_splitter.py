from __future__ import annotations

import re


PENDING_CONNECTOR_RE = re.compile(
    r"(?:^|[\s,;:—-])("
    r"for|since|because|although|while|when|if|as|just\s+as|so\s+that|"
    r"in\s+order\s+that|and|but|or|nor|which|who|whose|that|namely|"
    r"therefore|the|of|to|in|by|with|from|may|cease"
    r")\s*$",
    re.IGNORECASE,
)

TERMINAL_RE = re.compile(r"[.!?][\"')\]]*$")


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def has_terminal_punctuation(text: str) -> bool:
    return bool(TERMINAL_RE.search(text.rstrip()))


def ends_with_pending_connector(text: str) -> bool:
    tail = re.sub(r"\s+", " ", text.strip())[-160:]
    return bool(PENDING_CONNECTOR_RE.search(tail))


def is_safe_boundary(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped:
        return False
    if ends_with_pending_connector(stripped):
        return False
    return has_terminal_punctuation(stripped)


def _sentence_boundary_indexes(text: str) -> list[int]:
    indexes: list[int] = []
    for match in re.finditer(r"[.!?][\"')\]]?(?=\s+|$)", text):
        end = match.end()
        if is_safe_boundary(text[:end]):
            indexes.append(end)
    return indexes


def split_long_paragraph_sentence_aware(paragraph: str, max_chars: int) -> list[str]:
    cleaned = paragraph.strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    out: list[str] = []
    cursor = 0
    while cursor < len(cleaned):
        remaining = cleaned[cursor:].strip()
        if len(remaining) <= max_chars:
            out.append(remaining)
            break

        window_end = min(len(cleaned), cursor + max_chars)
        prefix = cleaned[cursor:window_end]
        boundaries = _sentence_boundary_indexes(prefix)
        if boundaries:
            cut = cursor + boundaries[-1]
        else:
            suffix = cleaned[cursor:]
            next_boundaries = _sentence_boundary_indexes(suffix)
            cut = cursor + next_boundaries[0] if next_boundaries else len(cleaned)

        piece = cleaned[cursor:cut].strip()
        if piece:
            out.append(piece)
        cursor = cut
        while cursor < len(cleaned) and cleaned[cursor].isspace():
            cursor += 1

    return out


def split_text_by_paragraphs_sentence_aware(text: str, max_chars: int) -> list[str]:
    cleaned = normalize_newlines(text).strip()
    if not cleaned:
        return []
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", cleaned) if item.strip()]
    if not paragraphs:
        return split_long_paragraph_sentence_aware(cleaned, max_chars)

    expanded: list[str] = []
    for paragraph in paragraphs:
        expanded.extend(split_long_paragraph_sentence_aware(paragraph, max_chars))

    chunks: list[str] = []
    current: list[str] = []
    current_chars = 0
    for paragraph in expanded:
        projected = current_chars + len(paragraph) + (2 if current else 0)
        current_is_heading = len(current) == 1 and bool(re.match(r"^\s*(?:#{1,6}\s*)?(BOOK|CHAPTER|Chapter|Book)\b", current[0]))
        if current and projected > max_chars and (is_safe_boundary(current[-1]) or current_is_heading):
            chunks.append("\n\n".join(current).strip() + "\n")
            current = [paragraph]
            current_chars = len(paragraph)
        else:
            current.append(paragraph)
            current_chars = projected
    if current:
        chunks.append("\n\n".join(current).strip() + "\n")
    return chunks
