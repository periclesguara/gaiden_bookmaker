from __future__ import annotations

from gaiden.application.editorial.us_english_spelling_pass import apply_us_english_spelling_pass


def test_replace_british_spellings_in_body_only():
    text = (
        "# Seneca’s Dialogues\n\n"
        "The splendour of his behaviour, valour, and saviour language.\n\n"
        "# Glossary\n\n"
        "<p id=\"g001\">splendour behaviour valour saviour</p>"
    )

    output, report = apply_us_english_spelling_pass(text)

    assert "The splendor of his behavior, valor, and savior language." in output
    assert '<p id="g001">splendour behaviour valour saviour</p>' in output
    assert report["summary"]["total_replacements"] == 4


def test_preserve_capitalized_forms():
    text = "# Seneca’s Dialogues\n\nSplendour Behaviour Valour Saviour\n\n# Glossary\n\nDone."

    output, report = apply_us_english_spelling_pass(text)

    assert "Splendor Behavior Valor Savior" in output
    assert report["summary"]["total_replacements"] == 4


def test_do_not_modify_glossary_byte_for_byte():
    glossary = "# Glossary\n\n<p id=\"g001\">splendour behaviour valour saviour</p>\n"
    text = "# Seneca’s Dialogues\n\nsplendour behaviour valour saviour\n\n" + glossary

    output, report = apply_us_english_spelling_pass(text)

    assert output.split("# Glossary", 1)[1] == text.split("# Glossary", 1)[1]
    assert report["validation"]["changed_after_glossary"] is False
    assert report["validation"]["glossary_byte_for_byte_unchanged"] is True


def test_do_not_modify_html_attributes():
    text = (
        "# Seneca’s Dialogues\n\n"
        '<p class="aphorism-number">01</p>\n'
        '<a href="#g001">G001</a>\n\n'
        "# Glossary\n\n"
        '<p id="g001"><a href="#ref-g001">↩</a></p>'
    )

    output, _report = apply_us_english_spelling_pass(text)

    assert '<p class="aphorism-number">01</p>' in output
    assert '<a href="#g001">G001</a>' in output


def test_preserve_chapter_headings():
    heading = "## Chapter 01 — To Marcia, on Consolation"
    text = f"# Seneca’s Dialogues\n\n{heading}\n\nsplendour\n\n# Glossary\n\nDone."

    output, _report = apply_us_english_spelling_pass(text)

    assert heading in output


def test_preserve_internal_markers():
    marker = '<p class="aphorism-number">01</p>'
    text = f"# Seneca’s Dialogues\n\n{marker}\n\nsplendour\n\n# Glossary\n\nDone."

    output, _report = apply_us_english_spelling_pass(text)

    assert marker in output


def test_report_zero_unauthorized_changes():
    text = "# Seneca’s Dialogues\n\nsplendour\n\n# Glossary\n\nDone."

    _output, report = apply_us_english_spelling_pass(text)

    assert report["summary"]["unauthorized_changes"] == 0
    assert report["validation"]["unauthorized_changed_lines"] == []


def test_validate_glossary_links():
    text = (
        "# Seneca’s Dialogues\n\n"
        'Rome<sup id="ref-g001"><a href="#g001">G001</a></sup> had splendour.\n\n'
        "# Glossary\n\n"
        '<p id="g001"><strong>G001 — Rome:</strong> City. <a href="#ref-g001">↩</a></p>'
    )

    output, report = apply_us_english_spelling_pass(text)

    assert "splendor" in output
    assert report["summary"]["glossary_links_valid"] is True
    assert report["validation"]["broken_hrefs"] == []


def test_validate_no_endnotes():
    text = "# Seneca’s Dialogues\n\nBody.\n\nEndnotes\n\n# Glossary\n\nDone."

    _output, report = apply_us_english_spelling_pass(text)

    assert report["summary"]["endnotes_remaining"] is True
    assert report["validation"]["passed"] is False


def test_full_kdp_shape_can_validate_when_present():
    chapters = "\n\n".join(f"## Chapter {number:02d} — Title {number}" for number in range(1, 13))
    text = f"# Seneca’s Dialogues\n\n{chapters}\n\nsplendour\n\n# Glossary\n\nDone."

    _output, report = apply_us_english_spelling_pass(text)

    assert report["summary"]["validation_passed"] is True
    assert report["validation"]["chapter_count"] == 12
