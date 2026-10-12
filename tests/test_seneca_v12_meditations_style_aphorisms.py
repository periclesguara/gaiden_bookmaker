from __future__ import annotations

from gaiden.application.structure.seneca_v12_meditations_style_aphorisms import (
    build_v12_meditations_style_aphorisms,
)


def test_remove_subchapter_marker_and_block_preserving_paragraph():
    text = (
        "# Seneca’s Dialogues\n\n"
        '<div class="chapter-page">\n\n## Chapter 01 — A\n\n</div>\n\n'
        '<div class="subchapter-block">\n\n'
        '<p class="subchapter-number">01</p>\n\n'
        "<p>Alpha.</p>\n\n"
        "</div>"
    )

    output, report = build_v12_meditations_style_aphorisms(text, preserve_glossary=False)

    assert '<div class="subchapter-block">' not in output
    assert '<p class="subchapter-number">01</p>' not in output
    assert '<p><span class="aphorism-inline-number">01.</span> Alpha.</p>' in output
    assert report["summary"]["subchapter_blocks_removed"] == 1
    assert report["summary"]["subchapter_markers_removed"] == 1


def test_number_every_body_paragraph_without_resetting_at_removed_subchapter():
    text = (
        "# Seneca’s Dialogues\n\n"
        "## Chapter 01 — A\n\n"
        '<p class="subchapter-number">01</p>\n\n'
        "Alpha.\n\n"
        "Beta.\n\n"
        '<p class="subchapter-number">02</p>\n\n'
        "<p>Gamma.</p>"
    )

    output, report = build_v12_meditations_style_aphorisms(text, preserve_glossary=False)

    assert '<span class="aphorism-inline-number">01.</span> Alpha.' in output
    assert '<span class="aphorism-inline-number">02.</span> Beta.' in output
    assert '<span class="aphorism-inline-number">03.</span> Gamma.' in output
    assert report["validation"]["chapter_aphorism_sequences"]["01"] == [1, 2, 3]


def test_reset_only_at_new_main_chapter():
    text = (
        "# Seneca’s Dialogues\n\n"
        "## Chapter 01 — A\n\n"
        "Alpha.\n\n"
        "Beta.\n\n"
        "## Chapter 02 — B\n\n"
        "Gamma."
    )

    output, report = build_v12_meditations_style_aphorisms(text, preserve_glossary=False)

    assert '<span class="aphorism-inline-number">01.</span> Alpha.' in output
    assert '<span class="aphorism-inline-number">02.</span> Beta.' in output
    assert '<span class="aphorism-inline-number">01.</span> Gamma.' in output
    assert report["validation"]["chapter_aphorism_sequences"]["02"] == [1]


def test_replace_old_inline_numbers_without_double_numbering():
    text = (
        "# Seneca’s Dialogues\n\n"
        "## Chapter 01 — A\n\n"
        '<p><span class="aphorism-inline-number">09.</span> Alpha.</p>\n\n'
        '<p><span class="aphorism-inline-number">44.</span> Beta.</p>'
    )

    output, report = build_v12_meditations_style_aphorisms(text, preserve_glossary=False)

    assert '<span class="aphorism-inline-number">09.</span>' not in output
    assert '<span class="aphorism-inline-number">44.</span>' not in output
    assert output.count('class="aphorism-inline-number"') == 2
    assert report["summary"]["double_numbered_paragraphs"] == 0


def test_preserve_chapter_page_wrapper():
    text = (
        "# Seneca’s Dialogues\n\n"
        '<div class="chapter-page">\n\n'
        "## Chapter 01 — A\n\n"
        "</div>\n\n"
        "Alpha."
    )

    output, _report = build_v12_meditations_style_aphorisms(text, preserve_glossary=False)

    assert '<div class="chapter-page">\n\n## Chapter 01 — A\n\n</div>' in output
    assert output.index("</div>") < output.index("Alpha.")


def test_preserve_glossary_byte_for_byte_and_do_not_number_it():
    glossary = '# Glossary\n\n<p id="g001">Alpha <a href="#ref-g001">↩</a></p>\n'
    text = (
        "# Seneca’s Dialogues\n\n"
        "## Chapter 01 — A\n\n"
        'Rome<sup id="ref-g001"><a href="#g001">G001</a></sup>.\n\n'
        + glossary
    )

    output, report = build_v12_meditations_style_aphorisms(text)

    assert output.split("# Glossary", 1)[1] == text.split("# Glossary", 1)[1]
    assert 'href="#g001"' in output
    assert 'href="#ref-g001"' in output
    assert report["summary"]["glossary_preserved"] is True
    assert report["summary"]["glossary_numbered"] is False


def test_do_not_create_toc_polluting_headings():
    text = (
        "# Seneca’s Dialogues\n\n"
        "## Chapter 01 — A\n\n"
        '<p class="subchapter-number">01</p>\n\n'
        "Alpha."
    )

    output, report = build_v12_meditations_style_aphorisms(text, preserve_glossary=False)

    assert "### 01" not in output
    assert report["summary"]["toc_polluting_headings"] == 0


def test_endnotes_fail_validation():
    text = "# Seneca’s Dialogues\n\n## Chapter 01 — A\n\nBody.\n\n# Endnotes\n\nKoch declares bad note."

    _output, report = build_v12_meditations_style_aphorisms(text, preserve_glossary=False)

    assert report["summary"]["endnotes_remaining"] is True
    assert report["summary"]["validation_passed"] is False


def test_body_prose_preserved():
    prose = "No sentence may be rewritten."
    text = (
        "# Seneca’s Dialogues\n\n"
        "## Chapter 01 — A\n\n"
        '<div class="subchapter-block">\n\n'
        '<p class="subchapter-number">01</p>\n\n'
        f"{prose}\n\n"
        "</div>"
    )

    output, report = build_v12_meditations_style_aphorisms(text, preserve_glossary=False)

    assert prose in output
    assert report["summary"]["body_prose_preserved"] is True
