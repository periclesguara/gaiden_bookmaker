from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List

DB = Path("data/db/gaiden.sqlite3")

def _conn():
    return sqlite3.connect(DB.as_posix())

def get_split_status(book_id: int) -> Dict:
    conn = _conn()
    try:
        row = conn.execute("""
          SELECT id, method, created_at
          FROM book_units
          WHERE book_id=? AND stage='split_struct'
        """, (book_id,)).fetchone()
        if not row:
            return {"exists": False}
        units_id = row[0]
        n = conn.execute("SELECT COUNT(*) FROM book_unit_items WHERE units_id=?", (units_id,)).fetchone()[0]
        return {"exists": True, "units_id": units_id, "method": row[1], "created_at": row[2], "count": n}
    finally:
        conn.close()

def list_units(book_id: int, limit: int = 300) -> List[Dict]:
    conn = _conn()
    try:
        row = conn.execute("SELECT id FROM book_units WHERE book_id=? AND stage='split_struct'", (book_id,)).fetchone()
        if not row:
            return []
        units_id = row[0]
        rows = conn.execute("""
          SELECT unit_index, unit_type, unit_title, start_line, end_line
          FROM book_unit_items
          WHERE units_id=?
          ORDER BY unit_index
          LIMIT ?
        """, (units_id, limit)).fetchall()
        return [
            {"unit_index": r[0], "unit_type": r[1], "unit_title": r[2], "start_line": r[3], "end_line": r[4]}
            for r in rows
        ]
    finally:
        conn.close()

def get_units_for_chunking(book_id: int):
    """
    Returns list of tuples: (unit_type, unit_title, start_line, end_line)
    """
    conn = _conn()
    try:
        row = conn.execute("SELECT id FROM book_units WHERE book_id=? AND stage='split_struct'", (book_id,)).fetchone()
        if not row:
            return None
        units_id = row[0]
        rows = conn.execute("""
          SELECT unit_type, unit_title, start_line, end_line
          FROM book_unit_items
          WHERE units_id=?
          ORDER BY unit_index
        """, (units_id,)).fetchall()
        return rows
    finally:
        conn.close()

def upsert_units(book_id: int, method: str):
    conn = _conn()
    try:
        conn.execute("""
          INSERT OR REPLACE INTO book_units (book_id, stage, method)
          VALUES (?, 'split_struct', ?)
        """, (book_id, method))
        conn.commit()
        units_id = conn.execute("SELECT id FROM book_units WHERE book_id=? AND stage='split_struct'", (book_id,)).fetchone()[0]
        return units_id
    finally:
        conn.close()

def replace_unit_items(units_id: int, items: List[Dict]):
    conn = _conn()
    try:
        conn.execute("DELETE FROM book_unit_items WHERE units_id=?", (units_id,))
        conn.executemany("""
          INSERT INTO book_unit_items (units_id, unit_index, unit_type, unit_title, start_line, end_line)
          VALUES (?, ?, ?, ?, ?, ?)
        """, [(units_id, it["unit_index"], it["unit_type"], it["unit_title"], it["start_line"], it["end_line"]) for it in items])
        conn.commit()
    finally:
        conn.close()
