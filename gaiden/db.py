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
        conn.execute(
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
        conn.commit()
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
