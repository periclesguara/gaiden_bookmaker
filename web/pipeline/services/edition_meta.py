from __future__ import annotations

from typing import Any


def book_code(edition: Any) -> str:
    if hasattr(edition, "work") and getattr(edition.work, "code", None):
        return edition.work.code
    return getattr(edition, "book_code", "")


def language_code(edition: Any) -> str:
    lang = getattr(edition, "language", None)
    if hasattr(lang, "code"):
        return lang.code
    return lang or ""


def title(edition: Any) -> str:
    edition_title = getattr(edition, "title", "") or ""
    if not edition_title and hasattr(edition, "work"):
        edition_title = getattr(edition.work, "title", "") or ""
    subtitle = getattr(edition, "subtitle", "") or ""
    if subtitle:
        return f"{edition_title} - {subtitle}".strip()
    return edition_title


def author_name(edition: Any) -> str:
    work = getattr(edition, "work", None)
    author = getattr(work, "author", None)
    if author and getattr(author, "name", None):
        return author.name
    return getattr(edition, "author_name", "")
