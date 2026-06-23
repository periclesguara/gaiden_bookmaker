from __future__ import annotations

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gaiden_portal.settings")
os.environ.setdefault("PGHOST", "127.0.0.1")
os.environ.setdefault("PGPORT", "5432")
os.environ.setdefault("PGDATABASE", "gaiden")
os.environ.setdefault("PGUSER", "gaiden")
os.environ.setdefault("PGPASSWORD", "gaiden")

import django

django.setup()

from editorial import kdp_mode
from editorial import frontmatter


def test_chapter_deduplication_is_scoped_by_book():
    source = """BOOK I

Chapter 1.

Book one chapter one.

Chapter 2.

Book one chapter two.

BOOK VII

Chapter II. Later book chapter two with a title-like tail.

Book seven chapter two.
"""

    output = kdp_mode._normalize_chapter_headings(source, "en")

    assert "# Book 01" in output
    assert "## Chapter 02.\n\nBook one chapter two." in output
    assert "# Book 07" in output
    assert "## Chapter 02.\n\nLater book chapter two with a title-like tail." in output
    assert output.index("# Book 01") < output.index("## Chapter 02.\n\nBook one chapter two.")
    assert output.index("# Book 07") < output.index("Later book chapter two with a title-like tail.")


def test_chapter_heading_prose_is_moved_to_body():
    source = """BOOK I

Chapter 03 - But to return from this digression...

Next paragraph.

Chapter 12. Well, we have now stated the nature...
"""

    output = kdp_mode._normalize_chapter_headings(source, "en")

    assert "## Chapter 03.\n\nBut to return from this digression..." in output
    assert "## Chapter 12.\n\nWell, we have now stated the nature..." in output
    assert "Chapter 03 - But" not in output
    assert "Chapter 12. Well" not in output


def test_long_structural_heading_validation_fails():
    text = "# Chapter 03 - " + ("Long prose " * 10)

    try:
        kdp_mode._assert_short_structural_headings(text, limit=60)
    except RuntimeError as exc:
        assert "heading longer than 60 characters" in str(exc)
    else:
        raise AssertionError("Expected long heading validation to fail.")


def test_any_long_heading_validation_fails():
    text = "## Aristotle: Life, Formation, and the Birth of Ethical Philosophy"

    try:
        kdp_mode._assert_short_structural_headings(text, limit=60)
    except RuntimeError as exc:
        assert "heading longer than 60 characters" in str(exc)
    else:
        raise AssertionError("Expected long heading validation to fail.")


def test_external_glossary_markers_are_linked_to_glossary_ids():
    entries = [{"id": "G01", "display": "political science", "category": "greek_term"}]

    output = kdp_mode._link_external_glossary_markers("political science ᴳ⁰¹ remains.", entries)

    assert "political science [ᴳ⁰¹](#glossary-g01) remains." == output


def test_external_glossary_markdown_has_stable_entry_anchors():
    entries = [
        {
            "id": "G01",
            "category": "greek_term",
            "display": "political science",
            "greek": "πολιτικὴ",
            "transliteration": "politikē",
            "english": "political science; statesmanship",
            "note": "The practical master science concerned with human good in the community.",
        }
    ]

    output = kdp_mode._build_external_glossary_markdown(entries)

    assert output.startswith("# Glossary")
    assert "### [G01] political science {#glossary-g01}" in output
    assert "**Greek:** πολιτικὴ" in output


def test_book_0029_editorial_titles_are_applied_only_to_book_headings():
    source = "# Book 01.\n\n## Chapter 01.\n\nText.\n\n# Book 10.\n\n## Chapter 02.\n"

    output = kdp_mode._apply_book_0029_editorial_titles(source)

    assert "# Book 01 — Happiness and the Human Good" in output
    assert "# Book 10 — Pleasure, Contemplation, and Final Happiness" in output
    assert "## Chapter 01." in output
    assert "## Chapter 02." in output


def test_book_0029_editorial_title_validation_rejects_wrong_title():
    source = "\n\n".join(
        f"# Book {num:02d} — {title}"
        for num, title in kdp_mode.BOOK_0029_EDITORIAL_TITLES.items()
    ).replace("Book 05 — Justice", "Book 05 — Wrong")

    try:
        kdp_mode._assert_book_0029_editorial_titles(source)
    except RuntimeError as exc:
        assert "Book title validation failed for Book 05" in str(exc)
    else:
        raise AssertionError("Expected wrong book title validation to fail.")


def test_frontmatter_nested_headings_stay_below_epub_split_level():
    output = frontmatter._demote_nested_frontmatter_headings(
        "## Aristotle: Life and Ethical Philosophy\n\nBody."
    )

    assert output.startswith("### Aristotle: Life and Ethical Philosophy")
