from __future__ import annotations

import sqlite3
from pathlib import Path

from gaiden.chunker import make_chunks_from_text, write_chunks

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

def fetch_book_language(book_id: int) -> str:
    conn = _conn()
    try:
        # language may be in books table depending on your schema; fallback en
        row = conn.execute("PRAGMA table_info(books)").fetchall()
        cols = [r[1] for r in row]
        if "language" in cols:
            r = conn.execute("SELECT language FROM books WHERE id=?", (book_id,)).fetchone()
            return (r[0] or "en") if r else "en"
        return "en"
    finally:
        conn.close()

def ensure_chunks_record(book_id: int, stage: str, method: str, target: int, min_t: int, max_t: int, language: str) -> int:
    conn = _conn()
    try:
        conn.execute("""
          INSERT OR REPLACE INTO book_chunks (book_id, stage, method, target_tokens, min_tokens, max_tokens, language)
          VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (book_id, stage, method, target, min_t, max_t, language))
        conn.commit()
        cid = conn.execute("SELECT id FROM book_chunks WHERE book_id=? AND stage=?", (book_id, stage)).fetchone()[0]
        return cid
    finally:
        conn.close()

def clear_chunk_items(chunks_id: int):
    conn = _conn()
    try:
        conn.execute("DELETE FROM book_chunk_items WHERE chunks_id=?", (chunks_id,))
        conn.commit()
    finally:
        conn.close()

def insert_chunk_item(chunks_id: int, c):
    conn = _conn()
    try:
        conn.execute("""
          INSERT INTO book_chunk_items (
            chunks_id, chunk_index, unit_type, unit_title,
            start_line, end_line, est_tokens, char_count, sha256, out_path
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            chunks_id, c.idx, c.unit_type, c.unit_title,
            c.start_line, c.end_line, c.est_tokens, c.char_count, c.sha256, c.out_path
        ))
        conn.commit()
    finally:
        conn.close()

def run_split_01(book_id: int, min_tokens: int = 1500, target_tokens: int = 1800, max_tokens: int = 2200) -> int:
    language = fetch_book_language(book_id)
    text = fetch_normalized(book_id)
    if not text.strip():
        raise ValueError("No normalized_text. Run normalize first.")

    stage = "split_01"
    method = "structure_token_chunks"

    chunks_id = ensure_chunks_record(book_id, stage, method, target_tokens, min_tokens, max_tokens, language)
    clear_chunk_items(chunks_id)

    chunks = make_chunks_from_text(text, language, min_tokens, target_tokens, max_tokens)
    chunks = write_chunks(book_id, stage, chunks)

    for c in chunks:
        insert_chunk_item(chunks_id, c)

    return len(chunks)
