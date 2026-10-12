from __future__ import annotations

import re
from pathlib import PurePosixPath


BOOK_CODE_RE = re.compile(r"\b(book_[0-9]{4,})\b", re.IGNORECASE)
HEADER_CODE_RE = re.compile(r"(?im)^\s*BOOK\s+CODE\s*:\s*(book_[0-9]{4,})\s*$")
TEXT_HEADER_RE = re.compile(r"(?im)^\s*(BOOK\s+CODE|TITLE|AUTHOR|LANGUAGE)\s*:\s*(.+?)\s*$")


def valid_book_code(value: str) -> bool:
    return bool(re.fullmatch(r"book_[0-9]{4,}", str(value or "").lower()))


def filename_metadata(name: str) -> dict[str, str]:
    stem = PurePosixPath(name).stem.strip()
    match = BOOK_CODE_RE.search(stem)
    code = match.group(1).lower() if match else ""
    remainder = stem[match.end():].strip(" _-—–") if match else stem
    parts = [part.strip() for part in re.split(r"\s+[—–]\s+|\s+-\s+", remainder) if part.strip()]
    if len(parts) >= 2:
        author, title = parts[0], " — ".join(parts[1:])
    else:
        author, title = "", (parts[0] if parts else remainder)
    return {"book_code": code, "title": title or stem, "author": author}


def header_book_code(data: bytes) -> str:
    return text_header_metadata(data)["book_code"]


def text_header_metadata(data: bytes) -> dict[str, str]:
    text = data[:65536].decode("utf-8", errors="replace")
    values: dict[str, str] = {}
    for key, value in TEXT_HEADER_RE.findall(text):
        normalized_key = key.upper().replace(" ", "_")
        values.setdefault(normalized_key, value.strip())
    code = values.get("BOOK_CODE", "").lower()
    return {
        "book_code": code if valid_book_code(code) else "",
        "title": values.get("TITLE", ""),
        "author": values.get("AUTHOR", ""),
        "language": values.get("LANGUAGE", ""),
    }


def normalize_language(value: str, fallback: str) -> str:
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", str(value or "")).strip().casefold().replace("_", "-")
    names = {
        "english": "en",
        "portuguese": "ptbr",
        "português": "ptbr",
        "spanish": "es",
        "español": "es",
        "german": "de",
        "deutsch": "de",
        "french": "fr",
        "français": "fr",
        "italian": "it",
        "italiano": "it",
    }
    if cleaned in names:
        return names[cleaned]
    primary = cleaned.split("-", 1)[0]
    if primary == "pt":
        return "ptbr"
    if primary in {"en", "es", "de", "fr", "it"}:
        return primary
    return fallback


def resolve_book_code(*, manifest_code: str = "", header_code: str = "", filename_code: str = "", linked_code: str = "", proposed_code: str = "") -> tuple[str, str]:
    supplied = [
        (source, code.lower())
        for source, code in (
            ("manifest", manifest_code),
            ("header", header_code),
            ("filename", filename_code),
            ("linked", linked_code),
        )
        if code
    ]
    invalid = [code for _source, code in supplied if not valid_book_code(code)]
    if invalid:
        return "", f"Código inválido: {invalid[0]}"
    distinct = {code for _source, code in supplied}
    if len(distinct) > 1:
        return "", "Fontes de identidade informam book_codes diferentes."
    if supplied:
        return supplied[0][1], ""
    if proposed_code and valid_book_code(proposed_code):
        return proposed_code.lower(), ""
    return "", "Nenhum book_code válido foi detectado ou proposto."
