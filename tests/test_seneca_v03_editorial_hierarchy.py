from __future__ import annotations

from gaiden.application.structure.seneca_v03_editorial_hierarchy import build_v03_editorial_hierarchy


def test_build_part_page_from_treatise_heading():
    text = "# Seneca’s Dialogues\n\n## On Leisure\n\n_To Serenus._\n\n### Part 8\n\nBody text."

    output, report = build_v03_editorial_hierarchy(text, expected_parts=1)

    assert '<div class="part-page">' in output
    assert "## Part I — On Leisure" in output
    assert "_To Serenus._" in output
    assert "### Section 1" in output
    assert "### Part 8" not in output
    assert report["summary"]["validation_passed"] is True


def test_number_naked_books_and_demote_chapters_under_books():
    text = (
        "# Seneca’s Dialogues\n\n"
        "## On Anger\n\n"
        "### Book\n\n"
        "#### Part 2\n\n"
        "##### Chapter 27\n\n"
        "Body text.\n\n"
        "### Book\n\n"
        "#### Part 3\n\n"
        "##### Chapter 28\n\n"
        "More text."
    )

    output, report = build_v03_editorial_hierarchy(text, expected_parts=1)

    assert "### Book I" in output
    assert "### Book II" in output
    assert "### Book\n" not in output
    assert "#### Chapter 27" in output
    assert "#### Chapter 28" in output
    assert "#### Part 2" not in output
    assert report["summary"]["books_inferred"] == 2
    assert report["summary"]["validation_passed"] is True


def test_promote_happy_life_plain_title_with_recipient():
    text = "# Seneca’s Dialogues\n\nOn a Happy Life\nTo Gallio.\n\n### Part 11\n\nBody text."

    output, report = build_v03_editorial_hierarchy(text, expected_parts=1)

    assert "## Part I — On the Happy Life" in output
    assert "_To Gallio._" in output
    assert "On a Happy Life\n" not in output
    assert "### Section 1" in output
    assert report["summary"]["validation_passed"] is True


def test_preserve_glossary_ids_and_links():
    text = (
        "# Seneca’s Dialogues\n\n"
        '## On Providence<sup id="ref-g047"><a href="#g047">G047</a></sup>\n\n'
        "_To Lucilius._\n\n"
        "### Part 9\n\n"
        "Body text.\n\n"
        "# Glossary\n\n"
        '<p id="g047"><a href="#ref-g047">↩</a> Providence definition.</p>'
    )

    output, report = build_v03_editorial_hierarchy(text, expected_parts=1)

    assert 'id="ref-g047"' in output
    assert 'href="#g047"' in output
    assert 'id="g047"' in output
    assert 'href="#ref-g047"' in output
    assert output.count("# Glossary") == 1
    assert report["summary"]["glossary_links_valid"] is True
    assert report["summary"]["validation_passed"] is True


def test_expected_part_mismatch_blocks_validation_without_inventing_parts():
    text = "# Seneca’s Dialogues\n\n## On Leisure\n\n_To Serenus._\n\n### Part 8\n\nBody text."

    output, report = build_v03_editorial_hierarchy(text, expected_parts=14)

    assert "## Part I — On Leisure" in output
    assert "## Part XIV" not in output
    assert report["summary"]["part_count"] == 1
    assert report["summary"]["validation_passed"] is False
    assert report["validation"]["missing_parts"] == [
        "II",
        "III",
        "IV",
        "V",
        "VI",
        "VII",
        "VIII",
        "IX",
        "X",
        "XI",
        "XII",
        "XIII",
        "XIV",
    ]


def test_raw_part_inside_existing_book_becomes_subsection_not_new_book():
    text = (
        "# Seneca’s Dialogues\n\n"
        "## On Benefits\n\n"
        "### Book\n\n"
        "#### Part 19\n\n"
        "Body.\n\n"
        "#### Part 20\n\n"
        "More body.\n\n"
        "### Book\n\n"
        "#### Part 21\n\n"
        "Final body."
    )

    output, report = build_v03_editorial_hierarchy(text, expected_parts=1)

    assert "### Book I" in output
    assert "### Book II" in output
    assert "### Book III" not in output
    assert "#### Section 1" in output
    assert report["summary"]["books_inferred"] == 2
    assert report["summary"]["validation_passed"] is True


def test_remove_endnotes_from_reader_file():
    text = "# Seneca’s Dialogues\n\n## On Leisure\n\nBody.\n\nEndnotes\n\nTranslator note."

    output, report = build_v03_editorial_hierarchy(text, expected_parts=1)

    assert "Translator note" not in output
    assert report["summary"]["endnotes_removed"] is True
    assert report["summary"]["validation_passed"] is True
