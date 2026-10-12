from __future__ import annotations

from gaiden.application.structure.seneca_v13_split_benefits_and_epilogue_pages import (
    build_v13_split_benefits_and_epilogue_pages,
)


def _paragraph(number: int) -> str:
    return f'<p><span class="aphorism-inline-number">{number:02d}.</span> Text {number}.</p>'


def test_split_on_benefits_into_six_chapters_and_renumber():
    benefits = "\n\n".join(_paragraph(number) for number in range(1, 618))
    text = (
        "# Seneca’s Dialogues\n\n"
        '<div class="chapter-page">\n\n## Chapter 12 — On Benefits\n\n</div>\n\n'
        f"{benefits}\n\n"
        "# Glossary\n<p id=\"g001\">Glossary <a href=\"#ref-g001\">↩</a></p>"
    )

    output, report = build_v13_split_benefits_and_epilogue_pages(text)

    assert "## Chapter 12 — On Benefits\n" not in output
    assert "## Chapter 12 — On Benefits I" in output
    assert "## Chapter 17 — On Benefits VI" in output
    assert report["summary"]["on_benefits_split_chapters"] == 6
    assert report["validation"]["on_benefits_total_aphorisms"] == 617
    assert report["validation"]["on_benefits_counts"]["12"] == 100
    assert report["validation"]["on_benefits_counts"]["17"] == 117
    assert '<span class="aphorism-inline-number">01.</span> Text 101.' in output
    assert '<span class="aphorism-inline-number">01.</span> Text 501.' in output


def test_preserve_glossary_byte_for_byte():
    benefits = "\n\n".join(_paragraph(number) for number in range(1, 618))
    glossary = '# Glossary\n<p id="g001">Glossary <a href="#ref-g001">↩</a></p>\n'
    text = (
        "# Seneca’s Dialogues\n\n"
        '<div class="chapter-page">\n\n## Chapter 12 — On Benefits\n\n</div>\n\n'
        f"{benefits}\n\n"
        f"{glossary}"
    )

    output, report = build_v13_split_benefits_and_epilogue_pages(text)

    assert output.split("# Glossary", 1)[1] == text.split("# Glossary", 1)[1]
    assert report["summary"]["glossary_preserved"] is True
