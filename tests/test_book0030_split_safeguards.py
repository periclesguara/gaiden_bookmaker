from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gaiden_portal.settings")
os.environ.setdefault("PGHOST", "127.0.0.1")
os.environ.setdefault("PGPORT", "5432")
os.environ.setdefault("PGDATABASE", "gaiden")
os.environ.setdefault("PGUSER", "gaiden")
os.environ.setdefault("PGPASSWORD", "gaiden")

import django

django.setup()

from pipeline.services import editorial_split, heading_cleaner


class _Chunk:
    def __init__(self, text: str) -> None:
        self.text = text


def test_heading_cleaner_removes_full_politics_contents_block():
    source = """A TREATISE ON GOVERNMENT
By Aristotle
CONTENTS
INTRODUCTION
BIBLIOGRAPHY
A TREATISE ON GOVERNMENT
BOOK I
CHAPTER I
CHAPTER II
BOOK II
CHAPTER I
INDEX
INTRODUCTION
The Politics of Aristotle is the second part of a treatise of which the
Ethics is the first part.
BOOK I
CHAPTER I
Every state is as we see a sort of partnership.
"""

    cleaned, stats = heading_cleaner._clean_normalized_text(source)

    assert stats["removed_toc_blocks"] == 1
    assert "CONTENTS" not in cleaned
    assert cleaned.count("INTRODUCTION") == 1
    assert "BIBLIOGRAPHY" not in cleaned
    assert "INDEX\nINTRODUCTION" not in cleaned
    assert "The Politics of Aristotle is the second part" in cleaned
    assert "Every state is as we see a sort of partnership." in cleaned


def test_split_coverage_detects_tiny_false_chapter_result():
    source = "A" * 1000
    chunks = [_Chunk("A" * 100), _Chunk("A" * 50)]

    assert editorial_split._text_coverage_ratio(source, chunks) == 0.15
