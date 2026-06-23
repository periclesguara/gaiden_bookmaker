from __future__ import annotations

from gaiden.application.editorial.surgical_polish import surgical_polish_text


def test_american_spelling_normalization():
    text = "labour honourable characterised defence theatre travelling grey"

    output, report = surgical_polish_text(text)

    assert output == "labor honorable characterized defense theater traveling gray"
    assert report["summary"]["spelling_replacements"] == 7


def test_modernity_cleanup():
    text = "Damn it—what madness this is, to punish oneself."

    output, report = surgical_polish_text(text)

    assert output == "Good gods, what madness this is, to punish oneself."
    assert report["summary"]["register_replacements"] == 1


def test_inline_heading_contamination():
    text = "ignorance would be as effective an excuse as innocence. CHAPTER 27 There are some things."

    output, report = surgical_polish_text(text)

    assert output == "ignorance would be as effective an excuse as innocence. There are some things."
    assert report["summary"]["inline_heading_contaminations_removed"] == 1


def test_inline_heading_before_comma():
    text = "Ptolemy CHAPTER 12, Cleopatra’s brother"

    output, _report = surgical_polish_text(text)

    assert output == "Ptolemy, Cleopatra’s brother"


def test_inline_heading_between_commas_collapses_duplicate_comma():
    text = "The lines are from Virgil, Aeneid, CHAPTER 8, 702."

    output, _report = surgical_polish_text(text)

    assert output == "The lines are from Virgil, Aeneid, 702."


def test_valid_headings_preserved():
    text = "Chapter 1\n\nText.\n\nChapter 2\n\nText.\n"

    output, report = surgical_polish_text(text)

    assert output == text
    assert report["validation"]["chapter_sequence_ok"] is True


def test_line_only_uppercase_heading_validation_fails():
    text = "Chapter 1\n\nText.\n\nCHAPTER 2\n\nText.\n"

    output, report = surgical_polish_text(text)

    assert output == text
    assert report["validation"]["line_only_uppercase_headings_remaining"] == 1
    assert report["validation"]["passed"] is False


def test_latin_terms_preserved():
    text = "sordida toga pulla praetexta sine insignibus Magistratus perversa vestis"

    output, report = surgical_polish_text(text)

    assert output == text
    assert report["summary"]["greek_or_latin_terms_detected"] >= 5


def test_broken_references_are_diagnostics_only():
    text = "See On Benefits, Book , Chapter 26."

    output, report = surgical_polish_text(text)

    assert output == text
    assert report["summary"]["broken_reference_warnings"] >= 1


def test_no_paragraph_rewrite():
    text = "Chapter 1\n\nThis is a paragraph with labour and honour.\n\nThis is another paragraph.\n"

    output, report = surgical_polish_text(text)

    assert output == "Chapter 1\n\nThis is a paragraph with labor and honor.\n\nThis is another paragraph.\n"
    assert report["summary"]["spelling_replacements"] == 2
