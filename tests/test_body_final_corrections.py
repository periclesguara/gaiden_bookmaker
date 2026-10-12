from __future__ import annotations

from gaiden.application.editorial.body_final_corrections import apply_body_only_corrections


def test_body_only_replacement():
    text = (
        "Chapter 1\n\n"
        "The splendour of Rome.\n\n"
        "# Glossary\n\n"
        '<p id="g001"><strong>G001 — splendour:</strong> Do not change this.</p>\n'
    )

    output, report = apply_body_only_corrections(text)

    assert "The splendor of Rome." in output
    assert '<strong>G001 — splendour:</strong>' in output
    assert report["replacements"]["splendour"]["count"] == 1
    assert report["validation"]["protected_part_unchanged"] is True


def test_multiple_replacements():
    text = "Chapter 1\n\nsplendour behaviour valour saviour\n\n# Glossary\n\nDone.\n"

    output, report = apply_body_only_corrections(text)

    assert "splendor behavior valor savior" in output
    assert report["summary"]["total_replacements"] == 4


def test_capitalized_replacements():
    text = "Chapter 1\n\nSplendour Behaviour Valour Saviour\n\n# Glossary\n\nDone.\n"

    output, report = apply_body_only_corrections(text)

    assert "Splendor Behavior Valor Savior" in output
    assert report["summary"]["total_replacements"] == 4


def test_protected_glossary_unchanged():
    protected = (
        "# Glossary\n\n"
        '<p id="g001"><strong>G001 — splendour:</strong> behaviour valour saviour.</p>\n'
    )
    text = "Chapter 1\n\nsplendour behaviour valour saviour\n\n" + protected

    output, report = apply_body_only_corrections(text)

    assert output.split("# Glossary", 1)[1] == protected.split("# Glossary", 1)[1]
    assert report["protected_part_unchanged"] is True
    assert report["summary"]["validation_passed"] is True

