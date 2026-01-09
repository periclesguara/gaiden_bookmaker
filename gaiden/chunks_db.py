from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Dict, Optional, Tuple

DB = Path("data/db/gaiden.sqlite3")

def _conn():
    return sqlite3.connect(DB.as_posix())

def get_split01_status(book_id: int) -> Dict:
    conn = _conn()
    try:
        row = conn.execute("""
          SELECT id, method, target_tokens, min_tokens, max_tokens, language, created_at
          FROM book_chunks
          WHERE book_id=? AND stage='split_01'
        """, (book_id,)).fetchone()
        if not row:
            return {"exists": False}
        chunks_id = row[0]
        n = conn.execute("SELECT COUNT(*) FROM book_chunk_items WHERE chunks_id=?", (chunks_id,)).fetchone()[0]
        return {
            "exists": True,
            "chunks_id": chunks_id,
            "method": row[1],
            "target_tokens": row[2],
            "min_tokens": row[3],
            "max_tokens": row[4],
            "language": row[5],
            "created_at": row[6],
            "count": n,
        }
    finally:
        conn.close()

def list_split01_chunks(book_id: int, limit: int = 200) -> List[Dict]:
    conn = _conn()
    try:
        row = conn.execute("SELECT id FROM book_chunks WHERE book_id=? AND stage='split_01'", (book_id,)).fetchone()
        if not row:
            return []
        chunks_id = row[0]
        rows = conn.execute("""
          SELECT chunk_index, unit_type, unit_title, est_tokens, char_count, out_path
          FROM book_chunk_items
          WHERE chunks_id=?
          ORDER BY chunk_index
          LIMIT ?
        """, (chunks_id, limit)).fetchall()
        out = []
        for r in rows:
            out.append({
                "chunk_index": r[0],
                "unit_type": r[1],
                "unit_title": r[2],
                "est_tokens": r[3],
                "char_count": r[4],
                "out_path": r[5],
            })
        return out
    finally:
        conn.close()
