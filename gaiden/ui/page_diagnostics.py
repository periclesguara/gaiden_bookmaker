from __future__ import annotations

import sqlite3
from pathlib import Path

import streamlit as st

from gaiden.diagnostics import get_book_diagnostics

DB_PATH = Path("data/db/gaiden.sqlite3")


def load_books():
    """
    Carrega lista de livros cadastrados no banco para o seletor.
    """
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(DB_PATH.as_posix())
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, title, seal FROM books ORDER BY id ASC"
        ).fetchall()
        return rows
    finally:
        conn.close()


def page():
    st.header("🩺 Gaiden — Pipeline diagnostics")

    books = load_books()
    if not books:
        st.info("Nenhum livro encontrado no banco ainda.")
        return

    # Label amigável: 0001 — Título (Selo)
    options = {
        f"{row['id']:04d} — {row['title']} ({row['seal']})": row["id"]
        for row in books
    }

    label = st.selectbox("Selecione um livro", list(options.keys()))
    book_id = options[label]

    diag = get_book_diagnostics(book_id)

    st.markdown(f"### 📘 Livro selecionado: `{book_id:04d}`")

    col1, col2, col3, col4, col5, col6 = st.columns(6)

    col1.metric("Livro indexado", "OK" if diag.indexed_ok else "NO")
    col2.metric(
        "Arquivo",
        "OK" if diag.file_ok else "NO",
        help=(diag.file_path.as_posix() if diag.file_path else "sem arquivo"),
    )
    col3.metric("Texto extraído", "OK" if diag.extracted_ok else "NO")
    col4.metric("Normalize", "OK" if diag.normalized_ok else "NO")
    col5.metric("Split", f"OK ({diag.split_units})" if diag.split_ok else "NO")
    col6.metric("Chunks", f"OK ({diag.chunk_count})" if diag.chunk_ok else "NO")

    st.caption(
        "Resumo do pipeline: livro indexado → arquivo salvo → extração de texto "
        "→ normalização → split estrutural → chunks de tokens."
    )

    ready = (
        diag.indexed_ok
        and diag.file_ok
        and diag.extracted_ok
        and diag.normalized_ok
        and diag.split_ok
        and diag.chunk_ok
    )

    if ready:
        st.success("✅ READY FOR OPENAI — todas as etapas básicas completas.")
    else:
        missing = []
        if not diag.indexed_ok:
            missing.append("indexed")
        if not diag.file_ok:
            missing.append("file")
        if not diag.extracted_ok:
            missing.append("extracted")
        if not diag.normalized_ok:
            missing.append("normalized")
        if not diag.split_ok:
            missing.append("split")
        if not diag.chunk_ok:
            missing.append("chunk")
        st.error(f"⚠️ Pendências: {', '.join(missing)}")
