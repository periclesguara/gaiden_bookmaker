from __future__ import annotations

from gaiden.chapter_agent_split import split_merged_text_into_chapters


def test_split_merged_text_ignores_plain_trailing_notes_with_book_headings():
    text = (
        "Chapter 1.\n\n"
        + ("Main body one. " * 120)
        + "\n\nChapter 2.\n\n"
        + ("Main body two. " * 120)
        + "\n\nNOTES\n\n"
        "BOOK I\n\n"
        "[1]\nTrailing note body.\n\n"
        "BOOK II\n\n"
        "[1]\nAnother trailing note.\n"
    )

    chapters = split_merged_text_into_chapters(text)

    assert [chapter["heading"] for chapter in chapters] == ["Chapter 1.", "Chapter 2."]
    assert "NOTES" not in chapters[-1]["text"]
    assert "BOOK I" not in chapters[-1]["text"]
