from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path("data/db/gaiden.sqlite3")


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH.as_posix())
    return conn


def init_db():
    conn = _connect()
    try:
        cur = conn.execute(
            """
            CREATE TABLE IF NOT EXISTS books (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              seal TEXT NOT NULL,
              title TEXT NOT NULL,
              author_original TEXT NOT NULL,
              collaborator_name TEXT,
              collaborator_roles TEXT,
              imprint_parent TEXT,
              lang TEXT,
              place TEXT,
              year TEXT,
              public_domain_origin INTEGER DEFAULT 0,
              about_work TEXT,
              about_contributor TEXT
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS book_files (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              book_id INTEGER NOT NULL,
              original_filename TEXT NOT NULL,
              ext TEXT NOT NULL,
              sha256 TEXT NOT NULL,
              stored_path TEXT NOT NULL,
              mime_type TEXT,
              size_bytes INTEGER,
              created_at TEXT DEFAULT (datetime('now')),
              FOREIGN KEY(book_id) REFERENCES books(id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS book_text (
              book_id INTEGER PRIMARY KEY,
              extracted_text TEXT,
              extracted_at TEXT DEFAULT (datetime('now')),
              FOREIGN KEY(book_id) REFERENCES books(id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS book_normalized (
              book_id INTEGER PRIMARY KEY,
              normalized_text TEXT,
              normalized_path TEXT,
              normalized_sha256 TEXT,
              normalized_at TEXT DEFAULT (datetime('now')),
              version TEXT DEFAULT 'v1',
              FOREIGN KEY(book_id) REFERENCES books(id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS book_split_items (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              book_id INTEGER NOT NULL,
              item_index INTEGER NOT NULL,
              item_type TEXT NOT NULL,
              label TEXT,
              token_count INTEGER,
              char_count INTEGER,
              path TEXT,
              parent_item_id INTEGER,
              level TEXT DEFAULT 'story',
              created_at TEXT DEFAULT (datetime('now')),
              FOREIGN KEY(book_id) REFERENCES books(id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS book_translated_merged (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              book_id INTEGER NOT NULL,
              lang_key TEXT NOT NULL,
              merged_path TEXT,
              merged_text TEXT,
              merged_sha256 TEXT,
              chunk_count INTEGER,
              source_dir TEXT,
              created_at TEXT DEFAULT (datetime('now')),
              UNIQUE (book_id, lang_key),
              FOREIGN KEY(book_id) REFERENCES books(id)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS book_polished_merged (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              book_id INTEGER NOT NULL,
              lang TEXT NOT NULL,
              variant TEXT NOT NULL,
              source_kind TEXT NOT NULL DEFAULT 'translated_merged',
              source_path TEXT NOT NULL,
              polished_path TEXT NOT NULL,
              model TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              FOREIGN KEY(book_id) REFERENCES books(id)
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_translated_merged(book_id: int, lang_key: str) -> sqlite3.Row:
    conn = _connect()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT id,
                   book_id,
                   lang_key,
                   merged_path,
                   merged_text,
                   merged_sha256,
                   chunk_count,
                   source_dir,
                   created_at
              FROM book_translated_merged
             WHERE book_id = ?
               AND lang_key = ?
            """,
            (book_id, lang_key),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(
                f"translated_merged not found for book_id={book_id}, lang_key={lang_key}"
            )
        return row
    finally:
        conn.close()


def insert_book(
    *,
    seal: str,
    title: str,
    author_original: str,
    collaborator_name: Optional[str],
    collaborator_roles: Optional[str],
    imprint_parent: Optional[str],
    lang: str,
    place: Optional[str],
    year: Optional[str],
    public_domain_origin: int = 0,
    about_work: str = "",
    about_contributor: str = "",
) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO books (
              seal,
              title,
              author_original,
              collaborator_name,
              collaborator_roles,
              imprint_parent,
              lang,
              place,
              year,
              public_domain_origin,
              about_work,
              about_contributor
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                seal,
                title,
                author_original,
                collaborator_name,
                collaborator_roles,
                imprint_parent,
                lang,
                place,
                year,
                public_domain_origin,
                about_work,
                about_contributor,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def insert_file(
    *,
    book_id: int,
    original_filename: str,
    ext: str,
    sha256: str,
    stored_path: str,
    mime_type: Optional[str],
    size_bytes: int,
) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO book_files (
              book_id,
              original_filename,
              ext,
              sha256,
              stored_path,
              mime_type,
              size_bytes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_id,
                original_filename,
                ext,
                sha256,
                stored_path,
                mime_type,
                size_bytes,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def upsert_extracted_text(book_id: int, text: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO book_text (book_id, extracted_text)
            VALUES (?, ?)
            ON CONFLICT(book_id) DO UPDATE SET
              extracted_text = excluded.extracted_text,
              extracted_at = datetime('now')
            """,
            (book_id, text),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_normalized_text(book_id: int, text: str, version: str = "v1") -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO book_normalized (
              book_id,
              normalized_text,
              version
            ) VALUES (?, ?, ?)
            ON CONFLICT(book_id) DO UPDATE SET
              normalized_text = excluded.normalized_text,
              version = excluded.version,
              normalized_at = datetime('now')
            """,
            (book_id, text, version),
        )
        conn.commit()
    finally:
        conn.close()


def update_book_about(book_id: int, about_work: str, about_contributor: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            UPDATE books
            SET about_work = ?, about_contributor = ?
            WHERE id = ?
            """,
            (about_work, about_contributor, book_id),
        )
        conn.commit()
    finally:
        conn.close()


def insert_polished_merged(
    *,
    book_id: int,
    lang: str,
    variant: str,
    source_kind: str,
    source_path: str,
    polished_path: str,
    model: str,
) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO book_polished_merged (
              book_id,
              lang,
              variant,
              source_kind,
              source_path,
              polished_path,
              model
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_id,
                lang,
                variant,
                source_kind,
                source_path,
                polished_path,
                model,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()
