from pathlib import Path

import pytest

from gaiden.infrastructure.converters.markitdown_adapter import MarkItDownAdapter


def test_markitdown_adapter_fails_for_missing_source(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        MarkItDownAdapter().convert_to_markdown(tmp_path / "missing.pdf")
