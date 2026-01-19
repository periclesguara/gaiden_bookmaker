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
    if hasattr(edition, "work") and getattr(edition.work, "title", None):
        return edition.work.title
    return getattr(edition, "title", "")


def author_name(edition: Any) -> str:
    work = getattr(edition, "work", None)
    author = getattr(work, "author", None)
    if author and getattr(author, "name", None):
        return author.name
    return getattr(edition, "author_name", "")
