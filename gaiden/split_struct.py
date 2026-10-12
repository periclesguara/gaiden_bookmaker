from __future__ import annotations

import sqlite3
from pathlib import Path

from gaiden.structure import detect_units
from gaiden.units_db import upsert_units, replace_unit_items

DB = Path("data/db/gaiden.sqlite3")

def _conn():
    return sqlite3.connect(DB.as_posix())

def fetch_normalized(book_id: int) -> str:
    conn = _conn()
    try:
        row = conn.execute("SELECT normalized_text FROM book_normalized WHERE book_id=?", (book_id,)).fetchone()
        return row[0] if row and row[0] else ""
    finally:
        conn.close()

def run_split_struct(book_id: int) -> int:
    text = fetch_normalized(book_id)
    if not text.strip():
        raise ValueError("Normalize required: no normalized_text found.")

    lines = text.splitlines()
    units = detect_units(lines)

    units_id = upsert_units(book_id, method="detect_units_v1")

    items = []
    for u in units:
        items.append({
            "unit_index": u.unit_index,
            "unit_type": u.unit_type,
            "unit_title": u.title,
            "start_line": u.start_line,
            "end_line": u.end_line,
        })

    replace_unit_items(units_id, items)
    return len(items)
