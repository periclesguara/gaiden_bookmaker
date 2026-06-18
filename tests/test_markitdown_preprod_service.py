from pathlib import Path

import pytest

from gaiden.application.ingest.markitdown_preprod_service import (
    clean_markitdown_markdown,
    run_markitdown_preprod,
)


class FakeConverter:
    def __init__(self, text: str = "# Title\n\n## Chapter 1\nBody\n"):
        self.text = text

    def convert_to_markdown(self, source_path: Path) -> str:
        return self.text


def test_cleaning_minimal_removes_trailing_spaces_and_excess_blank_lines():
    assert clean_markitdown_markdown("A  \r\n\n\n\n\nB") == "A\n\n\nB\n"


def test_service_fails_if_source_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_root = tmp_path / "data"
    for name in [
        "raw",
        "preprod",
        "normalized",
        "md",
        "chunks",
        "translated",
        "frontmatter",
        "images",
        "covers",
        "editions",
        "builds",
        "exports",
        "collections",
        "db",
        "tmp",
    ]:
        (data_root / name).mkdir(parents=True)
    monkeypatch.setenv("GAIDEN_DATA_ROOT", str(data_root))

    with pytest.raises(FileNotFoundError):
        run_markitdown_preprod("book_0001", "en", tmp_path / "missing.txt", converter=FakeConverter())


def test_service_generates_outputs_and_refuses_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_root = tmp_path / "data"
    for name in [
        "raw",
        "preprod",
        "normalized",
        "md",
        "chunks",
        "translated",
        "frontmatter",
        "images",
        "covers",
        "editions",
        "builds",
        "exports",
        "collections",
        "db",
        "tmp",
    ]:
        (data_root / name).mkdir(parents=True)
    monkeypatch.setenv("GAIDEN_DATA_ROOT", str(data_root))

    source_dir = data_root / "raw" / "book_0001" / "en"
    source_dir.mkdir(parents=True)
    source = source_dir / "source.txt"
    source.write_text("source", encoding="utf-8")

    result = run_markitdown_preprod("book_0001", "en", source, converter=FakeConverter())
    promoted = data_root / "md" / "book_0001" / "en" / "book_0001_en_source.md"

    assert result["status"] == "SUCCESS"
    assert promoted.exists()
    assert str(promoted).startswith(str(data_root))
    assert "web/data" not in str(promoted)

    with pytest.raises(FileExistsError):
        run_markitdown_preprod("book_0001", "en", source, converter=FakeConverter())

    result = run_markitdown_preprod("book_0001", "en", source, force=True, converter=FakeConverter("# New\n"))
    assert result["status"] in {"SUCCESS", "WARN"}
    assert promoted.read_text(encoding="utf-8") == "# New\n"
