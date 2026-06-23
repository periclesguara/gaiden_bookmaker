from __future__ import annotations

import json
from pathlib import Path

from gaiden.application.editorial import refine_cleanup_en_us as cleanup


def test_duplicate_adjacent_chapter_heading_is_removed():
    text = "BOOK I\n\nChapter 1.\n\nChapter 1.\n\nBody.\n"

    output, removed = cleanup.remove_duplicate_adjacent_headings(text)

    assert removed == 1
    assert output.count("Chapter 1.") == 1


def test_non_adjacent_repeated_chapter_heading_is_preserved():
    text = "BOOK I\n\nChapter 1.\n\nBody.\n\nChapter 1.\n\nAppendix-like mention.\n"

    output, removed = cleanup.remove_duplicate_adjacent_headings(text)

    assert removed == 0
    assert output.count("Chapter 1.") == 2


def test_book_order_validator_passes_for_i_through_x():
    text = "\n\n".join(f"BOOK {book}\n\nChapter 1.\n\nBody." for book in cleanup.EXPECTED_BOOK_ORDER)

    result = cleanup.validate_book_order(text)

    assert result["valid"] is True


def test_book_order_validator_fails_if_book_x_appears_before_book_ii():
    text = "BOOK I\n\nChapter 1.\n\nBOOK X\n\nChapter 1.\n\nBOOK II\n\nChapter 1.\n"

    result = cleanup.validate_book_order(text)

    assert result["valid"] is False


def test_greek_terms_are_detected_and_counted():
    text = "The term πολιτικὴ appears twice. πολιτικὴ differs from εἴδη."

    candidates = cleanup.detect_greek_glossary_candidates(text)
    by_term = {item["normalized_greek_term"]: item for item in candidates}

    assert by_term["πολιτικὴ"]["occurrence_count"] == 2
    assert by_term["εἴδη"]["occurrence_count"] == 1


def test_first_greek_occurrence_is_marked_once():
    text = "πολιτικὴ appears here. Later πολιτικὴ appears again."

    output, entries, marked = cleanup.replace_greek_with_english_markers(text)

    assert marked == 1
    assert output.count("[G01]") == 1
    assert "political science [G01]" in output
    assert "πολιτικὴ" not in output
    assert entries[0]["original"] == "πολιτικὴ"


def test_existing_note_markers_remain_unchanged(tmp_path: Path):
    input_path = tmp_path / "ordered.txt"
    input_path.write_text("BOOK I\n\nChapter 1.\n\nπολιτικὴ [1]\n\n" + _books_ii_to_x(), encoding="utf-8")
    clean_path = tmp_path / "clean.txt"
    report_path = tmp_path / "report.json"
    glossary_path = tmp_path / "glossary.json"

    report = cleanup.run_cleanup(
        input_path,
        clean_path=clean_path,
        report_path=report_path,
        glossary_path=glossary_path,
        replacements_config=Path("data/config/refine_cleanup_en_us_replacements.json"),
    )

    assert report["status"] == "PASSED"
    assert "[1]" in clean_path.read_text(encoding="utf-8")
    assert json.loads(report_path.read_text(encoding="utf-8"))["note_markers_preserved"] is True
    glossary = json.loads(glossary_path.read_text(encoding="utf-8"))
    assert glossary["entries"][0]["id"] == "G01"
    assert (tmp_path / "glossary.md").exists()


def test_approved_terminology_replacements_are_applied_exactly_and_only_from_config(tmp_path: Path):
    config = tmp_path / "replacements.json"
    config.write_text(
        json.dumps(
            {
                "replacements": [
                    {"from": "perfected Self-Mastery", "to": "temperance"},
                    {"from": "complete lack of self-control", "to": "intemperance"},
                ]
            }
        ),
        encoding="utf-8",
    )
    text = "perfected Self-Mastery; complete lack of self-control; virtue; happiness; Chief Good; right reason"

    output, report = cleanup.apply_replacements(text, cleanup.load_replacements(config))

    assert "temperance" in output
    assert "intemperance" in output
    assert "virtue" in output
    assert "happiness" in output
    assert "Chief Good" in output
    assert "right reason" in output
    assert sum(item["count"] for item in report) == 2


def test_proper_names_are_marked_and_added_to_glossary():
    text = "BOOK I\n\nChapter 1.\n\nPlato and Sardanapalus are cited. Plato appears again."

    output, entries, marked = cleanup.mark_proper_names(text, [])

    assert marked == 2
    assert "Plato [N01]" in output
    assert output.count("Plato") == 2
    assert "Sardanapalus [N02]" in output
    assert [entry["id"] for entry in entries] == ["N01", "N02"]


def _books_ii_to_x() -> str:
    return "\n\n".join(f"BOOK {book}\n\nChapter 1.\n\nBody." for book in cleanup.EXPECTED_BOOK_ORDER[1:])
