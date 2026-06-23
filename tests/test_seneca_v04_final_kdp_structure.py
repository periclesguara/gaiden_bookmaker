from __future__ import annotations

from pathlib import Path

from gaiden.application.structure.seneca_v04_final_kdp_structure import build_v04_final_kdp_structure


def test_creates_only_chapter_headings_for_major_treatises():
    text = (
        '<div class="part-page">\n\n'
        "## Part I — To Marcia, on Consolation\n\n"
        "</div>\n\n"
        "### Chapter 1\n\n"
        "Text."
    )

    output, report = build_v04_final_kdp_structure(text, preserve_glossary=False)

    assert (
        '<div class="chapter-page">\n\n'
        "## Chapter 01 — To Marcia, on Consolation\n\n"
        "</div>\n\n"
        '<p class="aphorism-number">01</p>\n\n'
        "Text."
    ) in output
    assert "## Part" not in output
    assert "### Chapter" not in output
    assert report["validation"]["reader_chapter_count"] == 1


def test_internal_numbers_are_not_markdown_headings():
    text = "## Part I — A\n\n### Chapter 1\n\nText."

    output, _report = build_v04_final_kdp_structure(text, preserve_glossary=False)

    assert "### 01" not in output
    assert '<p class="aphorism-number">01</p>' in output


def test_flatten_books_and_sections():
    text = (
        "## Part I — A\n\n"
        "### Book I\n\n"
        "#### Section 1\n\n"
        "Text A.\n\n"
        "### Book II\n\n"
        "#### Section 1\n\n"
        "Text B."
    )

    output, report = build_v04_final_kdp_structure(text, preserve_glossary=False)

    assert "### Book" not in output
    assert "#### Section" not in output
    assert output.count('<p class="aphorism-number">') == 2
    assert '<p class="aphorism-number">01</p>' in output
    assert '<p class="aphorism-number">02</p>' in output
    assert report["chapters"][0]["internal_blocks"] == 2


def test_do_not_create_empty_markers():
    text = "## Part I — A\n\n### Book I\n\n#### Section 1\n\nText."

    output, report = build_v04_final_kdp_structure(text, preserve_glossary=False)

    assert output.count('<p class="aphorism-number">') == 1
    assert report["validation"]["empty_internal_blocks"] == []


def test_reset_internal_numbering_per_chapter():
    text = "## Part I — A\n\n### Chapter 1\n\nText A.\n\n## Part II — B\n\n### Chapter 1\n\nText B."

    output, report = build_v04_final_kdp_structure(text, preserve_glossary=False)

    assert "## Chapter 01 — A" in output
    assert "## Chapter 02 — B" in output
    assert output.count('<p class="aphorism-number">01</p>') == 2
    assert report["validation"]["internal_numbering_valid"] is True


def test_remove_glossary_markers_from_headings():
    text = (
        '## Part VIII — On Providence<sup id="ref-g047"><a href="#g047">G047</a></sup>\n\n'
        "### Section 1\n\n"
        "Text."
    )

    output, report = build_v04_final_kdp_structure(text, preserve_glossary=False)

    assert "## Chapter 01 — On Providence" in output
    assert "## Chapter 01 — On Providence<sup" not in output
    assert report["summary"]["heading_glossary_markers_removed"] == 1


def test_preserve_body_glossary_links():
    text = (
        "## Part I — A\n\n"
        "### Chapter 1\n\n"
        'Rome<sup id="ref-g001"><a href="#g001">G001</a></sup> remains linked.\n\n'
        "# Glossary\n\n"
        '<p id="g001"><strong>G001 — Rome:</strong> City. <a href="#ref-g001">↩</a></p>'
    )

    output, report = build_v04_final_kdp_structure(text)

    assert 'Rome<sup id="ref-g001"><a href="#g001">G001</a></sup>' in output
    assert report["summary"]["glossary_links_valid"] is True


def test_preserve_glossary_entries_unchanged():
    glossary = '<p id="g001"><strong>G001 — Rome:</strong> City. <a href="#ref-g001">↩</a></p>'
    text = (
        "## Part I — A\n\n"
        "### Chapter 1\n\n"
        'Rome<sup id="ref-g001"><a href="#g001">G001</a></sup>.\n\n'
        "# Glossary\n\n"
        f"{glossary}"
    )

    output, report = build_v04_final_kdp_structure(text)

    assert "# Glossary" in output
    assert glossary in output
    assert report["summary"]["glossary_preserved"] is True


def test_no_endnotes():
    text = "## Part I — A\n\n### Chapter 1\n\nText.\n\n# Endnotes\n\nKoch note. ↩︎"

    output, report = build_v04_final_kdp_structure(text, preserve_glossary=False)

    assert "# Endnotes" not in output
    assert "Koch note" not in output
    assert "↩︎" not in output
    assert report["validation"]["endnotes_remaining"] is False


def test_no_source_headings_remain():
    text = (
        "## Part I — A\n\n"
        "### Book I\n\n"
        "### Chapter 1\n\n"
        "### Section 1\n\n"
        "### Aphorism 1\n\n"
        "Text."
    )

    output, report = build_v04_final_kdp_structure(text, preserve_glossary=False)

    assert "## Part" not in output
    assert "### Book" not in output
    assert "### Chapter" not in output
    assert "### Section" not in output
    assert "### Aphorism" not in output
    assert report["validation"]["source_headings_remaining"] == []


def test_body_prose_preserved():
    prose = "No paragraph text may be rewritten."
    text = f"## Part I — A\n\n### Chapter 1\n\n{prose}"

    output, report = build_v04_final_kdp_structure(text, preserve_glossary=False)

    assert prose in output
    assert report["validation"]["body_prose_preserved"] is True


def test_full_file_reader_chapter_count_if_input_exists():
    path = Path("data/builds/book_0026/en/dialogues_seneca_v03_editorial_hierarchy.md")
    if not path.exists():
        return

    _output, report = build_v04_final_kdp_structure(path.read_text(encoding="utf-8"))

    assert report["summary"]["reader_chapters_created"] == 12
