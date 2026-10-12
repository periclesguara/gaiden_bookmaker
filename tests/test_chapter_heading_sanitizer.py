from __future__ import annotations

import pytest

from gaiden.application.structure.chapter_heading_sanitizer import sanitize_chapter_headings


def test_valid_sequence_remains_valid():
    text = "Chapter 1\n\nText.\n\nChapter 2\n\nText.\n\nChapter 3\n\nText.\n"

    fixed, report = sanitize_chapter_headings(text)

    assert fixed == text
    assert [item["chapter"] for item in report["accepted"]] == [1, 2, 3]
    assert report["removed"] == []


def test_duplicate_backward_heading_is_removed():
    text = (
        "Chapter 1\n\nText.\n\nChapter 2\n\nText.\n\nChapter 3\n\nText.\n\n"
        "CHAPTER 2\n\nMore text.\n\nChapter 4\n\nText.\n"
    )

    fixed, report = sanitize_chapter_headings(text)

    assert "CHAPTER 2" not in fixed
    assert "More text." in fixed
    assert [item["chapter"] for item in report["accepted"]] == [1, 2, 3, 4]
    assert report["removed"] == [
        {
            "line": 13,
            "original": "CHAPTER 2",
            "chapter": 2,
            "reason": "backward_or_duplicate_heading",
        }
    ]


def test_uppercase_valid_heading_is_normalized():
    text = "Chapter 1\n\nText.\n\nCHAPTER 2\n\nText.\n"

    fixed, report = sanitize_chapter_headings(text)

    assert fixed == "Chapter 1\n\nText.\n\nChapter 2\n\nText.\n"
    assert [item["normalized"] for item in report["accepted"]] == ["Chapter 1", "Chapter 2"]
    assert report["removed"] == []


def test_skipped_heading_is_removed_when_gaps_are_disallowed():
    text = "Chapter 1\n\nText.\n\nChapter 3\n\nText.\n"

    fixed, report = sanitize_chapter_headings(text)

    assert fixed == "Chapter 1\n\nText.\n\nText.\n"
    assert [item["chapter"] for item in report["accepted"]] == [1]
    assert report["removed"][0]["original"] == "Chapter 3"
    assert report["removed"][0]["reason"] == "skipped_heading"
    assert report["warnings"]


def test_preserve_prose_exactly_around_removed_heading():
    text = "Chapter 1\n\nParagraph before.\n\nCHAPTER 1\n\nParagraph after.\n"

    fixed, report = sanitize_chapter_headings(text)

    assert fixed == "Chapter 1\n\nParagraph before.\n\nParagraph after.\n"
    assert report["removed"][0]["original"] == "CHAPTER 1"
    assert report["removed"][0]["reason"] == "backward_or_duplicate_heading"


def test_real_book_0026_contamination_sample():
    text = (
        "Chapter 1\n\nText.\n\nChapter 2\n\nText.\n\nChapter 3\n\nText.\n\n"
        "CHAPTER 2\n"
        "Choose, therefore, which of these two examples you find more commendable.\n\n"
        "Chapter 4\n\nText.\n"
    )

    fixed, report = sanitize_chapter_headings(text)

    assert "CHAPTER 2" not in fixed
    assert "Choose, therefore, which of these two examples you find more commendable." in fixed
    assert [item["chapter"] for item in report["accepted"]] == [1, 2, 3, 4]
    assert report["removed"][0]["original"] == "CHAPTER 2"


def test_validation_raises_if_accepted_sequence_is_invalid_with_allowed_gaps():
    text = "Chapter 1\n\nText.\n\nChapter 3\n\nText.\n"

    with pytest.raises(ValueError):
        sanitize_chapter_headings(text, allow_gaps=True)
