from __future__ import annotations

from gaiden.application.glossary.glossary_integrator import (
    build_final_file,
    integrate_glossary_into_body,
    parse_glossary_md,
)


def test_parse_simple_entry():
    entries = parse_glossary_md("**Rome** - Capital of the Roman world. Category: place.")

    assert entries[0]["primary_term"] == "Rome"
    assert entries[0]["definition"] == "Capital of the Roman world."
    assert entries[0]["category"] == "place"


def test_parse_alias_entry():
    entries = parse_glossary_md(
        "**Sejanus (Lucius Aelius Sejanus)** - Powerful commander. Category: person."
    )

    assert entries[0]["primary_term"] == "Sejanus"
    assert "Lucius Aelius Sejanus" in entries[0]["aliases"]
    assert entries[0]["definition"] == "Powerful commander."
    assert entries[0]["category"] == "person"


def test_insert_first_marker():
    body = "Chapter 1\n\nMarcia grieved.\n"
    entries = parse_glossary_md("**Marcia** - Roman woman addressed by Seneca. Category: person.")

    output, linked, _report = integrate_glossary_into_body(body, entries)

    assert 'Marcia<sup id="ref-g001"><a href="#g001">G001</a></sup>' in output
    assert linked[0]["matched_text"] == "Marcia"


def test_append_glossary_section():
    body = "Chapter 1\n\nMarcia grieved.\n"
    glossary = "**Marcia** - Roman woman addressed by Seneca. Category: person."

    output, report = build_final_file(body, glossary)

    assert "# Glossary" in output
    assert (
        '<p id="g001"><strong>G001 — Marcia:</strong> Roman woman addressed by Seneca. '
        '<a href="#ref-g001">↩</a></p>'
    ) in output
    assert report["validation"]["passed"] is True


def test_remove_visible_category():
    body = "Chapter 1\n\nRome was powerful.\n"
    glossary = "**Rome** - Capital of the Roman world. Category: place."

    output, _report = build_final_file(body, glossary)

    assert "Category:" not in output


def test_first_occurrence_only():
    body = "Chapter 1\n\nRome was powerful. Rome endured.\n"
    entries = parse_glossary_md("**Rome** - Capital of the Roman world. Category: place.")

    output, _linked, _report = integrate_glossary_into_body(body, entries)

    assert output.count('href="#g001"') == 1


def test_longest_term_first():
    body = "Chapter 1\n\nAulus Cremutius Cordus was remembered. Cordus was praised.\n"
    glossary = (
        "**Aulus Cremutius Cordus** - Roman historian. Category: person.\n"
        "**Cordus** - Short form. Category: person.\n"
    )
    entries = parse_glossary_md(glossary)

    output, _linked, _report = integrate_glossary_into_body(body, entries)

    assert 'Aulus Cremutius Cordus<sup id="ref-g001"><a href="#g001">G001</a></sup>' in output
    assert 'Cordus<sup id="ref-g002"><a href="#g002">G002</a></sup> was praised' in output
    assert 'Aulus Cremutius Cordus<sup id="ref-g002"' not in output


def test_do_not_link_heading():
    body = "# Rome\n\nRome was powerful.\n"
    entries = parse_glossary_md("**Rome** - Capital of the Roman world. Category: place.")

    output, _linked, _report = integrate_glossary_into_body(body, entries)

    assert output.startswith("# Rome\n")
    assert output.count('href="#g001"') == 1
    assert 'Rome<sup id="ref-g001"' in output.splitlines()[2]


def test_do_not_link_endnotes():
    body = "Chapter 1\n\nRome was powerful.\n\n# Endnotes\n\nRome appears in a note.\n"
    entries = parse_glossary_md("**Rome** - Capital of the Roman world. Category: place.")

    output, _linked, _report = integrate_glossary_into_body(body, entries)

    assert output.count('href="#g001"') == 1
    assert 'Rome appears in a note.' in output


def test_backlinks_work():
    body = "Chapter 1\n\nMarcia grieved.\n"
    glossary = "**Marcia** - Roman woman addressed by Seneca. Category: person."

    output, report = build_final_file(body, glossary)

    assert 'id="ref-g001"' in output
    assert 'id="g001"' in output
    assert 'href="#g001"' in output
    assert 'href="#ref-g001"' in output
    assert report["validation"]["broken_hrefs"] == []

