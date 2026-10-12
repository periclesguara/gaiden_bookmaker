from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

DB_PATH = Path("data/db/gaiden.sqlite3")
CHUNKS_ROOT = Path("data/chunks")


@dataclass
class BookDiagnostics:
    book_id: int
    indexed_ok: bool
    file_ok: bool
    file_path: Path | None
    extracted_ok: bool
    normalized_ok: bool
    split_ok: bool
    split_units: int
    chunk_ok: bool
    chunk_count: int


def get_book_diagnostics(book_id: int) -> BookDiagnostics:
    indexed_ok = False
    file_ok = False
    file_path: Path | None = None
    extracted_ok = False
    normalized_ok = False
    split_ok = False
    split_units = 0
    chunk_ok = False
    chunk_count = 0

    if not DB_PATH.exists():
        return BookDiagnostics(
            book_id=book_id,
            indexed_ok=False,
            file_ok=False,
            file_path=None,
            extracted_ok=False,
            normalized_ok=False,
            split_ok=False,
            split_units=0,
            chunk_ok=False,
            chunk_count=0,
        )

    conn = sqlite3.connect(DB_PATH.as_posix())
    conn.row_factory = sqlite3.Row
    try:
        # books
        row = conn.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone()
        indexed_ok = row is not None

        # file
        row = conn.execute(
            "SELECT stored_path FROM book_files WHERE book_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (book_id,),
        ).fetchone()
        if row:
            p = Path(row["stored_path"])
            file_path = p
            file_ok = p.exists()

        # extracted text
        row = conn.execute(
            "SELECT extracted_text FROM book_text WHERE book_id = ?",
            (book_id,),
        ).fetchone()
        if row and row["extracted_text"]:
            extracted_ok = True

        # normalized text
        try:
            row = conn.execute(
                "SELECT normalized_text FROM book_normalized WHERE book_id = ?",
                (book_id,),
            ).fetchone()
            if row and row["normalized_text"]:
                normalized_ok = True
        except sqlite3.OperationalError:
            # tabela não existe ainda
            normalized_ok = False

        # split units
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM book_split_items WHERE book_id = ?",
                (book_id,),
            ).fetchone()
            if row:
                split_units = int(row["c"] or 0)
                split_ok = split_units > 0
        except sqlite3.OperationalError:
            split_units = 0
            split_ok = False

    finally:
        conn.close()

    # chunks (filesystem)
    chunks_dir = CHUNKS_ROOT / f"book_{book_id:04d}" / "split_01"
    if chunks_dir.exists():
        files = list(chunks_dir.glob("*.txt"))
        chunk_count = len(files)
        chunk_ok = chunk_count > 0

    return BookDiagnostics(
        book_id=book_id,
        indexed_ok=indexed_ok,
        file_ok=file_ok,
        file_path=file_path,
        extracted_ok=extracted_ok,
        normalized_ok=normalized_ok,
        split_ok=split_ok,
        split_units=split_units,
        chunk_ok=chunk_ok,
        chunk_count=chunk_count,
    )
