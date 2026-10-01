from decimal import Decimal

import pytest

from gaiden.print_engine.manuscript import manuscript_tex
from gaiden.print_engine.isbn import isbn13
from gaiden.print_engine.providers.industrial import REQUIRED_CHECKS, validate_industrial
from gaiden.print_engine.specs import PrintSpec


def test_composition_preserves_source_order_without_invented_frontmatter():
    source = "# Chapter One\n\nThe first sentence.\n\n# Chapter Two\n\nThe last sentence.\n"
    result = manuscript_tex(source, PrintSpec())
    assert result.index("The first sentence.") < result.index("The last sentence.")
    assert "Published by" not in result
    assert "\\printendmark{The End}" in result
    assert source == "# Chapter One\n\nThe first sentence.\n\n# Chapter Two\n\nThe last sentence.\n"


@pytest.mark.parametrize("source", ["unstructured prose", "# Chapter\n\n![art](image.png)", "# Title\n\n<table>content</table>", "# Title\n\n**important**"])
def test_unsupported_structure_is_not_silently_removed(source):
    with pytest.raises(ValueError):
        manuscript_tex(source, PrintSpec())


def test_tex_commands_are_printed_as_text():
    result = manuscript_tex("# Chapter\n\n\\input{private} & 50%", PrintSpec())
    assert "\\input{private}" not in result
    assert r"\textbackslash{}input\{private\}" in result


def test_intake_pagebreak_blocks_render_as_print_pagebreaks():
    source = "# Frontmatter\n\nFirst page.\n\n::: pagebreak\n:::\n\n# Chapter\n\nBody."
    result = manuscript_tex(source, PrintSpec())
    assert "\\clearpage" in result
    assert ":::" not in result


def test_frontispiece_and_approved_illustration_have_dedicated_print_pages():
    source = (
        "# Frontispiece\n\n"
        "AUTHOR NAME\n\n"
        "### THE DEVIL IN PARIS\n\n"
        "*A Sherlock Holmes Novel*\n\n"
        "RINOBOOKS — Rio de Janeiro · Brazil · 2026\n\n"
        "::: pagebreak\n:::\n\n"
        "# Copyright\n\nCopyright text.\n\n"
        "## Chapter 01.\n\nChapter text."
    )

    result = manuscript_tex(source, PrintSpec(), image_manifest={1: "illustrations/FIGURE_001.jpg"})

    assert "\\frontispiecepage" in result
    assert "\\closefrontispiecepage" in result
    assert "\\illustrationpage{1}{illustrations/FIGURE_001.jpg}" in result
    assert "\\mainmatter" in result
    assert "\\chaptertitle{Chapter text.}" in result
    assert "\\chapterheading{" not in result
    assert "### THE DEVIL IN PARIS" not in result
    assert result.count("RINOBOOKS") == 1


def test_portuguese_print_ends_with_fim():
    result = manuscript_tex("# Capítulo\n\nTexto.", PrintSpec(), language_code="pt-br")

    assert "\\printendmark{Fim}" in result
    assert "\\printendmark{The End}" not in result


def test_units_remain_inches():
    spec = PrintSpec(trim_width_in="5.5", trim_height_in="8.5")
    assert spec.trim_width_in * Decimal("25.4") == Decimal("139.70")


@pytest.mark.parametrize("value", ["9783161484100", "9791090636071"])
def test_valid_isbn13_is_normalized(value):
    assert isbn13(value) == value


@pytest.mark.parametrize("value", ["9783161484101", "123", "978-ABC"])
def test_invalid_isbn13_is_rejected(value):
    with pytest.raises(ValueError):
        isbn13(value)


def test_missing_industrial_validator_is_a_blocker(tmp_path):
    evidence = validate_industrial("", tmp_path / "job.json", {"interior": "a"})

    assert evidence["tool_version"] == ""
    assert evidence["checks"][0]["severity"] == "BLOCKER"


def test_industrial_validator_requires_complete_hash_bound_evidence(tmp_path):
    job = tmp_path / "job.json"
    job.write_text("{}", encoding="utf-8")
    validator = tmp_path / "validator"
    checks = ",".join(
        f'{{"code":"{code}","severity":"INFO","message":"verified"}}'
        for code in sorted(REQUIRED_CHECKS)
    )
    validator.write_text(
        "#!/bin/sh\n"
        "printf '%s' '{\"hashes\":{\"interior\":\"a\"},\"tool_version\":\"test-1\","
        "\"pdfx_standard\":\"PDF/X-1a:2001\",\"checks\":[" + checks + "]}'\n",
        encoding="utf-8",
    )
    validator.chmod(0o700)

    evidence = validate_industrial(str(validator), job, {"interior": "a"})

    assert evidence["tool_version"] == "test-1"
    assert {item["code"] for item in evidence["checks"]} == REQUIRED_CHECKS
