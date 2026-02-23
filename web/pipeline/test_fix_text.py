from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from pipeline.services import fix_text


def _edition_stub(book_code: str = "book_0005", lang: str = "en"):
    return SimpleNamespace(
        work=SimpleNamespace(code=book_code),
        language=SimpleNamespace(code=lang),
    )


def _write_normalized_md(root: Path, book_code: str, lang: str, content: str) -> Path:
    path = root / "data" / "normalized" / book_code / lang / "normalized.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_heading_missing_is_fail():
    before = [
        fix_text.HeadingItem(level=1, key="chapter::a", text="Chapter 01 - A", line_no=1),
        fix_text.HeadingItem(level=1, key="chapter::b", text="Chapter 02 - B", line_no=2),
    ]
    after = [
        fix_text.HeadingItem(level=1, key="chapter::a", text="Chapter 01 - A", line_no=1),
    ]
    result = fix_text.compare_heading_inventory(before, after)
    assert result["ok"] is False
    assert result["reason"] == "heading_missing_after_fix"


def test_heading_created_is_fail():
    before = [
        fix_text.HeadingItem(level=1, key="chapter::a", text="Chapter 01 - A", line_no=1),
    ]
    after = [
        fix_text.HeadingItem(level=1, key="chapter::a", text="Chapter 01 - A", line_no=1),
        fix_text.HeadingItem(level=1, key="chapter::b", text="Chapter 02 - B", line_no=2),
    ]
    result = fix_text.compare_heading_inventory(before, after)
    assert result["ok"] is False
    assert result["reason"] == "heading_created_after_fix"


def test_chapter_roman_is_converted_and_passes():
    edition = _edition_stub()
    with TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        _write_normalized_md(root, "book_0005", "en", "# CHAPTER IV. The Thing\nBody line\n")
        with patch.object(fix_text.canonical_index, "project_root", return_value=root), patch.object(
            fix_text.canonical_index, "_git_text", return_value="ok"
        ):
            report = fix_text.fix_text(edition)
        out = root / report["output_path"]
        assert report["status"] == "PASS"
        assert "# Chapter 04 - The Thing" in out.read_text(encoding="utf-8")


def test_isolated_numeric_line_is_removed():
    edition = _edition_stub()
    with TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        _write_normalized_md(root, "book_0005", "en", "# Chapter I. Intro\n12\nBody line\n")
        with patch.object(fix_text.canonical_index, "project_root", return_value=root), patch.object(
            fix_text.canonical_index, "_git_text", return_value="ok"
        ):
            report = fix_text.fix_text(edition)
        out_text = (root / report["output_path"]).read_text(encoding="utf-8")
        assert report["status"] == "PASS"
        assert "\n12\n" not in out_text


def test_louis_xiv_in_paragraph_is_preserved():
    edition = _edition_stub()
    with TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        _write_normalized_md(
            root,
            "book_0005",
            "en",
            "# CHAPTER II. Court\nLouis XIV was king of France.\n",
        )
        with patch.object(fix_text.canonical_index, "project_root", return_value=root), patch.object(
            fix_text.canonical_index, "_git_text", return_value="ok"
        ):
            report = fix_text.fix_text(edition)
        out_text = (root / report["output_path"]).read_text(encoding="utf-8")
        assert report["status"] == "PASS"
        assert "Louis XIV was king of France." in out_text
