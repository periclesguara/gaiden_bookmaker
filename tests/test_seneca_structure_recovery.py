from __future__ import annotations

from gaiden.application.structure.seneca_structure_recovery import recover_seneca_structure


def test_promote_treatise_title():
    text = "Chapter 43\n\nFinal paragraph.\n\nTo Helvia, on Consolation\n\nPART 5\n\nBody text."

    output, _notes, report = recover_seneca_structure(text)

    assert "### Chapter 43\n\nFinal paragraph.\n\n## To Helvia, on Consolation\n\n### Part 5\n\nBody text." in output
    assert report["summary"]["plain_treatise_titles_remaining"] == 0


def test_promote_inline_title_and_part():
    text = "On Leisure To Serenus. PART 8\n\nBody text."

    output, _notes, _report = recover_seneca_structure(text)

    assert "## On Leisure\n\n_To Serenus._\n\n### Part 8\n\nBody text." in output


def test_preserve_body_text():
    prose = "This sentence must not be rewritten."
    output, _notes, _report = recover_seneca_structure(f"Chapter 1\n\n{prose}")

    assert prose in output


def test_remove_broken_endnotes_from_reader_file():
    text = (
        "# Glossary\n\n"
        '<p id="g001">Glossary entry.</p>\n\n'
        "Endnotes See Merivale’s History of the Romans Under the Empire , Chapter . ↩︎"
    )

    output, raw_notes, report = recover_seneca_structure(text)

    assert "# Glossary" in output
    assert "Endnotes See Merivale" in raw_notes
    assert "Endnotes See Merivale" not in output
    assert report["summary"]["raw_endnotes_preserved"] is True


def test_preserve_glossary_links():
    text = (
        'Chapter 1\n\nMarcia<sup id="ref-g001"><a href="#g001">G001</a></sup>\n\n'
        '# Glossary\n\n<p id="g001"><a href="#ref-g001">↩</a></p>'
    )

    output, _notes, report = recover_seneca_structure(text)

    assert 'Marcia<sup id="ref-g001"><a href="#g001">G001</a></sup>' in output
    assert report["summary"]["glossary_links_valid"] is True


def test_detect_residue_before_endnotes():
    text = "Chapter 1\n\nKoch declares that this cannot be the true reading."

    _output, _notes, report = recover_seneca_structure(text)

    assert report["possible_residues_before_endnotes"]


def test_no_plain_part_remains():
    text = "To Helvia, on Consolation\n\nPART 5\n\nBody text."

    output, _notes, report = recover_seneca_structure(text)

    assert "\nPART 5\n" not in output
    assert "### Part 5" in output
    assert report["summary"]["plain_part_markers_remaining"] == 0


def test_no_giant_chapter_43_container():
    text = "Chapter 43\n\nFinal.\n\nTo Helvia, on Consolation\n\nPART 5\n\nBody."

    output, _notes, _report = recover_seneca_structure(text)

    assert output.index("## To Helvia, on Consolation") > output.index("### Chapter 43")


def test_output_valid_utf8():
    output, raw_notes, _report = recover_seneca_structure("Chapter 1\n\nText.")

    output.encode("utf-8")
    raw_notes.encode("utf-8")

