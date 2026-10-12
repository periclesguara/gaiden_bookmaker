from __future__ import annotations

from gaiden.application.structure.seneca_v11_pagination_rules import (
    REQUIRED_CSS,
    apply_v11_pagination_rules,
)


def test_chapter_page_contains_only_title_by_moving_text_outside():
    text = '<div class="chapter-page">\n\n## Chapter 01 — On Anger\n\nText should not be here.\n\n</div>'

    output, report = apply_v11_pagination_rules(text, preserve_glossary=False)

    assert '<div class="chapter-page">\n\n## Chapter 01 — On Anger\n\n</div>' in output
    assert output.index("Text should not be here.") > output.index("</div>")
    assert report["validation"]["chapter_pages_with_body_text"] == 0


def test_chapter_page_css_forces_standalone_page():
    assert "page-break-before: always" in REQUIRED_CSS
    assert "break-before: page" in REQUIRED_CSS
    assert "page-break-after: always" in REQUIRED_CSS
    assert "break-after: page" in REQUIRED_CSS


def test_epilogue_section_page_contains_only_title():
    text = "## Stoicism\n\nStoicism text."

    output, report = apply_v11_pagination_rules(text, preserve_glossary=False)

    assert '<div class="epilogue-section-page">\n\n## Stoicism\n\n</div>\n\nStoicism text.' in output
    assert report["summary"]["epilogue_section_pages_wrapped"] == 1


def test_subchapter_marker_sticks_to_first_paragraph():
    text = (
        '<p class="subchapter-number">01</p>\n\n'
        '<p><span class="aphorism-inline-number">01.</span> Text A.</p>\n\n'
        '<p><span class="aphorism-inline-number">02.</span> Text B.</p>'
    )

    output, report = apply_v11_pagination_rules(text, preserve_glossary=False)

    assert '<div class="subchapter-block">' in output
    assert '<p class="subchapter-number">01</p>' in output
    assert '<p><span class="aphorism-inline-number">01.</span> Text A.</p>' in output
    assert report["summary"]["subchapter_blocks_wrapped"] == 1


def test_do_not_wrap_entire_subchapter():
    text = (
        '<p class="subchapter-number">01</p>\n\n'
        '<p><span class="aphorism-inline-number">01.</span> Text A.</p>\n\n'
        '<p><span class="aphorism-inline-number">02.</span> Text B.</p>'
    )

    output, report = apply_v11_pagination_rules(text, preserve_glossary=False)
    block = output.split('<div class="subchapter-block">', 1)[1].split("</div>", 1)[0]

    assert "Text B." not in block
    assert "Text B." in output.split("</div>", 1)[1]
    assert report["validation"]["subchapter_block_errors"] == []


def test_convert_existing_aphorism_marker_to_subchapter_block():
    text = '<p class="aphorism-number">01</p>\n\nFirst paragraph.'

    output, report = apply_v11_pagination_rules(text, preserve_glossary=False)

    assert '<p class="subchapter-number">01</p>' in output
    assert '<p><span class="aphorism-inline-number">01.</span> First paragraph.</p>' in output
    assert '<p class="aphorism-number">01</p>' not in output
    assert report["summary"]["subchapter_blocks_wrapped"] == 1


def test_do_not_modify_glossary():
    glossary = "# Glossary\n\n<p id=\"g001\">Glossary entry <a href=\"#ref-g001\">↩</a></p>\n"
    text = "## Chapter 01 — A\n\nBody.\n\n" + glossary

    output, report = apply_v11_pagination_rules(text)

    assert output.split("# Glossary", 1)[1] == text.split("# Glossary", 1)[1]
    assert report["summary"]["glossary_preserved"] is True


def test_do_not_create_internal_headings():
    text = '<p class="aphorism-number">01</p>\n\nText.'

    output, report = apply_v11_pagination_rules(text, preserve_glossary=False)

    assert "### 01" not in output
    assert "### Section" not in output
    assert "### Aphorism" not in output
    assert report["summary"]["toc_polluting_headings"] == 0


def test_preserve_body_links():
    text = (
        '## Chapter 01 — A\n\n'
        '<p class="aphorism-number">01</p>\n\n'
        'Rome<sup id="ref-g001"><a href="#g001">G001</a></sup>.\n\n'
        '# Glossary\n\n'
        '<p id="g001">Rome <a href="#ref-g001">↩</a></p>'
    )

    output, report = apply_v11_pagination_rules(text)

    assert 'href="#g001"' in output
    assert 'href="#ref-g001"' in output
    assert report["summary"]["glossary_links_valid"] is True


def test_no_endnotes():
    text = "## Chapter 01 — A\n\nBody.\n\n# Endnotes\n\nKoch declares bad note."

    _output, report = apply_v11_pagination_rules(text, preserve_glossary=False)

    assert report["summary"]["endnotes_remaining"] is True
    assert report["summary"]["validation_passed"] is False


def test_body_prose_preserved():
    prose = "No sentence may be rewritten."
    text = f'<p class="aphorism-number">01</p>\n\n{prose}'

    output, report = apply_v11_pagination_rules(text, preserve_glossary=False)

    assert prose in output
    assert report["summary"]["body_prose_preserved"] is True
