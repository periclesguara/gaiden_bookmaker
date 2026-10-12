from __future__ import annotations

import json
from pathlib import Path

from gaiden.application.editorial import final_superscript_cleanup as cleanup


def test_converts_glossary_markers_to_superscript_and_preserves_notes():
    text = "political science [G01] refers to Plato [N01]. See [1] and [43]."

    output, total, by_prefix = cleanup.convert_markers(text)

    assert total == 2
    assert by_prefix["G"] == 1
    assert by_prefix["N"] == 1
    assert "political science ᴳ⁰¹" in output
    assert "Plato ᴺ⁰¹" in output
    assert "[1]" in output
    assert "[43]" in output


def test_exact_fixes_are_applied_after_marker_conversion():
    text = (
        "middle-men ᴳ⁰⁸, or middle-men. "
        "The phrase the the noble ᴳ³⁶ appears. "
        "those [movements ᴳ³⁴ or comings-to-be ᴳ³⁵] that tend"
    )

    output, fixes = cleanup.apply_exact_fixes(text)

    assert "middle-men ᴳ⁰⁸, or middle-men" not in output
    assert "middle-men ᴳ⁰⁸" in output
    assert "the the noble" not in output
    assert "the noble ᴳ³⁶" in output
    assert "those movements ᴳ³⁴, or comings-to-be ᴳ³⁵, that tend" in output
    assert fixes == {
        "middle_men_duplicate_removed": 1,
        "the_the_noble_fixed": 1,
        "nested_movements_brackets_fixed": 1,
    }


def test_validation_fails_for_raw_greek_and_old_markers():
    text = _book_text("BOOK I\n\nChapter 1.\n\nπολιτικὴ [G01]\n\n")

    validation, _warnings, errors = cleanup.validate_text(text, text)

    assert validation["raw_greek_remaining"] == 1
    assert validation["square_bracket_glossary_markers_remaining"] == 1
    assert "raw_greek_remaining" in errors
    assert "square_bracket_glossary_markers_remaining" in errors


def test_validation_fails_if_book_x_precedes_book_ii():
    text = "BOOK I\n\nChapter 1.\n\nBOOK X\n\nChapter 1.\n\nBOOK II\n\nChapter 1.\n"

    validation, _warnings, errors = cleanup.validate_text(text, text)

    assert validation["book_order_valid"] is False
    assert "book_order_invalid" in errors


def test_run_cleanup_writes_output_only_when_validation_passes(tmp_path: Path):
    input_path = tmp_path / "input.txt"
    input_path.write_text(
        _book_text(
            "BOOK I\n\nChapter 1.\n\n"
            "political science [G01] [1]. middle-men [G08], or middle-men. "
            "the the noble [G36]. those [movements [G34] or comings-to-be [G35]] that tend.\n\n"
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "final.txt"
    report_path = tmp_path / "report.json"

    report = cleanup.run_cleanup(input_path, output_path=output_path, report_path=report_path)

    assert report["status"] == "PASSED"
    output = output_path.read_text(encoding="utf-8")
    assert "[G01]" not in output
    assert "political science ᴳ⁰¹ [1]" in output
    assert "middle-men ᴳ⁰⁸, or middle-men" not in output
    assert "the noble ᴳ³⁶" in output
    assert json.loads(report_path.read_text(encoding="utf-8"))["markers_converted_total"] == 5


def test_run_cleanup_does_not_overwrite_output_when_validation_fails(tmp_path: Path):
    input_path = tmp_path / "input.txt"
    input_path.write_text(_book_text("BOOK I\n\nChapter 1.\n\nGreek term [G01]\n\n"), encoding="utf-8")
    output_path = tmp_path / "final.txt"
    output_path.write_text("previous good output\n", encoding="utf-8")
    report_path = tmp_path / "report.json"

    report = cleanup.run_cleanup(input_path, output_path=output_path, report_path=report_path)

    assert report["status"] == "FAILED"
    assert output_path.read_text(encoding="utf-8") == "previous good output\n"
    assert "greek_placeholders_remaining" in report["errors"]


def _book_text(book_i_body: str) -> str:
    rest = "\n\n".join(f"BOOK {book}\n\nChapter 1.\n\nBody." for book in cleanup.EXPECTED_BOOK_ORDER[1:])
    return book_i_body + rest + "\n"
