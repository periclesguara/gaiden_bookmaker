from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import streamlit as st

from gaiden.db import init_db
from gaiden.templates import BookMeta, frontispiece_text, copyright_page
from gaiden.split_01 import run_split_01
from gaiden_key_manager import page_key_manager  # <-- integração Key Manager


# -------------------------------------------------------------------
# CONFIG DB
# -------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "db" / "gaiden.sqlite3"

init_db()


def get_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


# -------------------------------------------------------------------
# HELPERS DE CONTAGEM SEGURA
# -------------------------------------------------------------------

def _safe_count(table: str, book_id: int) -> int:
    """
    Conta linhas em uma tabela com coluna book_id, sem quebrar se a tabela não existir.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                f"SELECT COUNT(*) FROM {table} WHERE book_id = ?",
                (book_id,),
            )
        except sqlite3.OperationalError:
            return 0
        row = cur.fetchone()
        return row[0] if row else 0


def get_split_count(book_id: int) -> int:
    """
    Soma contagens de tabelas relacionadas a split:
    - book_split_items (token-level)
    - book_units ou book_unit_items (structure-level), se quiser considerar também
    """
    total = 0
    total += _safe_count("book_split_items", book_id)
    total += _safe_count("book_units", book_id)
    total += _safe_count("book_unit_items", book_id)
    return total


def get_chunk_count(book_id: int) -> int:
    """
    Soma contagens de tabelas relacionadas a chunks:
    - book_chunk_items
    - book_chunks
    """
    total = 0
    total += _safe_count("book_chunk_items", book_id)
    total += _safe_count("book_chunks", book_id)
    return total


def has_split(book_id: int) -> bool:
    return get_split_count(book_id) > 0


def has_chunks(book_id: int) -> bool:
    return get_chunk_count(book_id) > 0


# -------------------------------------------------------------------
# QUERIES BÁSICAS
# -------------------------------------------------------------------

def get_books_basic():
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                id,
                title,
                seal,
                author_original
            FROM books
            ORDER BY id ASC
            """
        )
        return cur.fetchall()


def get_books_with_meta():
    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT
                    id,
                    title,
                    seal,
                    author_original,
                    COALESCE(collaborator_name, '')           AS collaborator_name,
                    COALESCE(collaborator_role, '')           AS collaborator_role,
                    COALESCE(place, '')                       AS place,
                    COALESCE(year, 0)                         AS year,
                    COALESCE(lang, 'en')                      AS lang,
                    COALESCE(public_domain_origin, 0)         AS public_domain_origin,
                    COALESCE(about_work, '')                  AS about_work,
                    COALESCE(about_contributor, '')           AS about_contributor
                FROM books
                ORDER BY id ASC
                """
            )
            rows = cur.fetchall()
            return rows, True
        except sqlite3.OperationalError:
            cur.execute(
                """
                SELECT
                    id,
                    title,
                    seal,
                    author_original
                FROM books
                ORDER BY id ASC
                """
            )
            rows = cur.fetchall()
            return rows, False


def update_book_metadata(
    book_id: int,
    title: str,
    seal: str,
    author_original: str,
    collaborator_name: str | None,
    collaborator_roles: list[str],
    place: str,
    year: int,
    lang: str,
    public_domain_origin: bool,
    about_work: str,
    about_contributor: str,
) -> None:
    roles_str = json.dumps(collaborator_roles, ensure_ascii=False)

    with get_connection() as conn:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE books
                SET
                    title = ?,
                    seal = ?,
                    author_original = ?,
                    collaborator_name = ?,
                    collaborator_role = ?,
                    place = ?,
                    year = ?,
                    lang = ?,
                    public_domain_origin = ?,
                    about_work = ?,
                    about_contributor = ?
                WHERE id = ?
                """,
                (
                    title,
                    seal,
                    author_original,
                    collaborator_name,
                    roles_str,
                    place,
                    year,
                    lang,
                    1 if public_domain_origin else 0,
                    about_work,
                    about_contributor,
                    book_id,
                ),
            )
            conn.commit()
        except sqlite3.OperationalError:
            # schema antigo: ignora, não trava UI
            pass


def get_normalized_text(book_id: int) -> str | None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT normalized_text
            FROM book_normalized
            WHERE book_id = ?
            """,
            (book_id,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def has_normalized(book_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM book_normalized WHERE book_id = ? LIMIT 1",
            (book_id,),
        )
        return cur.fetchone() is not None


# -------------------------------------------------------------------
# PÁGINA DEBUG — RAW
# -------------------------------------------------------------------

def page_debug_books():
    st.header("DEBUG — Conteúdo bruto da tabela books")

    rows = get_books_basic()
    if not rows:
        st.warning("Tabela books está vazia.")
        return

    st.write("Registros encontrados:")
    for r in rows:
        book_id, title, seal, author = r
        st.text(f"{book_id:04d} | {title} | {seal} | {author}")


# -------------------------------------------------------------------
# PÁGINA 1 — BOOKS & TEMPLATES
# -------------------------------------------------------------------

ROLE_OPTIONS = ["Adapter", "Translator", "Reviewer", "Curator"]
LANG_OPTIONS = ["en", "pt", "es"]


def decode_roles(raw: str) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data]
        if isinstance(data, str):
            return [data]
    except json.JSONDecodeError:
        pass
    return [x.strip() for x in raw.split(",") if x.strip()]


def page_books_templates():
    st.header("1 — Books & Templates")

    rows, has_meta = get_books_with_meta()
    if not rows:
        st.info("Nenhum livro cadastrado.")
        return

    options = {f"{r[0]:04d} — {r[1]}": r for r in rows}
    label = st.selectbox("Select a book", list(options.keys()))
    r = options[label]

    if has_meta:
        (
            book_id,
            title,
            seal,
            author_original,
            collaborator_name,
            collaborator_role_raw,
            place,
            year,
            lang,
            public_domain_origin,
            about_work_db,
            about_contributor_db,
        ) = r
    else:
        book_id, title, seal, author_original = r
        collaborator_name = ""
        collaborator_role_raw = ""
        place = "Rio de Janeiro - Brasil"
        year = 2026
        lang = "en"
        public_domain_origin = 0
        about_work_db = ""
        about_contributor_db = ""

    st.write(f"**Book ID:** {book_id}")

    st.subheader("Metadata & Credits")

    col_main1, col_main2 = st.columns(2)

    with col_main1:
        title_val = st.text_input("Title", value=title)
        seal_val = st.text_input("Imprint / Seal", value=seal)
        author_val = st.text_input("Original author", value=author_original)

        collaborator_name_val = st.text_input(
            "Main collaborator name", value=collaborator_name
        )

    with col_main2:
        roles_default = decode_roles(collaborator_role_raw)
        roles_selected = st.multiselect(
            "Collaborator roles",
            ROLE_OPTIONS,
            default=[r for r in roles_default if r in ROLE_OPTIONS],
            help="Você pode marcar Adapter / Translator / Reviewer / Curator",
        )

        lang_val = st.selectbox(
            "Language of this edition",
            LANG_OPTIONS,
            index=LANG_OPTIONS.index(lang) if lang in LANG_OPTIONS else 0,
        )

        place_val = st.text_input("Place", value=place or "Rio de Janeiro - Brasil")
        year_val = st.number_input(
            "Year",
            min_value=1800,
            max_value=2100,
            value=int(year) if year else 2026,
            step=1,
        )

        public_domain_val = st.checkbox(
            "Based on public domain source",
            value=bool(public_domain_origin),
        )

    st.subheader("About texts")

    about_work_val = st.text_area(
        "About this work / edition",
        value=about_work_db or "",
        height=120,
    )

    about_contrib_val = st.text_area(
        "About the contributor",
        value=about_contributor_db or "",
        height=120,
    )

    if st.button("Salvar metadados", type="primary"):
        update_book_metadata(
            book_id=book_id,
            title=title_val,
            seal=seal_val,
            author_original=author_val,
            collaborator_name=collaborator_name_val or None,
            collaborator_roles=roles_selected,
            place=place_val,
            year=int(year_val),
            lang=lang_val,
            public_domain_origin=public_domain_val,
            about_work=about_work_val,
            about_contributor=about_contrib_val,
        )
        st.success("Metadados atualizados (quando colunas existem no schema).")

        title = title_val
        seal = seal_val
        author_original = author_val
        collaborator_name = collaborator_name_val
        place = place_val
        year = int(year_val)
        lang = lang_val
        public_domain_origin = 1 if public_domain_val else 0
        about_work_db = about_work_val
        about_contributor_db = about_contrib_val
        collaborator_role_raw = json.dumps(roles_selected, ensure_ascii=False)

    meta = BookMeta(
        title=title_val,
        author_original=author_val,
        seal=seal_val,
        place=place_val,
        year=int(year_val) if year_val else 2026,
        lang=lang_val,
        collaborator_name=collaborator_name_val or None,
        collaborator_role=", ".join(roles_selected) if roles_selected else None,
        parent_imprint="RinoBooks",
        about_work=about_work_val or None,
        about_contributor=about_contrib_val or None,
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### Frontispiece Preview")
        st.code(frontispiece_text(meta))

    with col2:
        st.markdown("#### Copyright Preview")
        st.code(copyright_page(meta))

    st.markdown("#### Normalized text (sample)")
    norm = get_normalized_text(book_id)
    if norm:
        st.text_area("First 1500 chars of normalized text", norm[:1500], height=200)
    else:
        st.warning("Ainda não há texto normalizado para este livro.")


# -------------------------------------------------------------------
# PÁGINA 2 — PIPELINE (Normalize / Split / Chunk)
# -------------------------------------------------------------------

def page_pipeline():
    st.header("2 — Pipeline (Normalize / Split / Chunk)")

    rows = get_books_basic()
    if not rows:
        st.info("Nenhum livro cadastrado.")
        return

    options = {f"{r[0]:04d} — {r[1]}": r for r in rows}
    label = st.selectbox("Select book", list(options.keys()))
    r = options[label]
    book_id = r[0]

    st.write(f"**Book ID:** {book_id}")

    colA, colB, colC = st.columns(3)

    # NORMALIZE
    with colA:
        st.subheader("01 — Normalize")
        st.write("Status: " + ("✅ OK" if has_normalized(book_id) else "❌ NO"))
        if st.button("Run normalize", key=f"normalize_{book_id}"):
            st.warning("TODO: plugar aqui a função normalize_text_v2(book_id).")

    # SPLIT
    with colB:
        st.subheader("02 — Split (structure)")
        split_count = get_split_count(book_id)
        st.write(
            f"Status: {'✅ OK' if split_count > 0 else '❌ NO'} "
            f"(items: {split_count})"
        )
        if st.button("Run split_01", key=f"split_{book_id}"):
            n = run_split_01(
                book_id,
                min_tokens=1500,
                target_tokens=1800,
                max_tokens=2200,
            )
            st.success(f"SPLIT criado com sucesso: {n} itens.")

    # CHUNK
    with colC:
        st.subheader("03 — Chunk")
        chunk_count = get_chunk_count(book_id)
        st.write(
            f"Status: {'✅ OK' if chunk_count > 0 else '❌ NO'} "
            f"(items: {chunk_count})"
        )
        if st.button("Run chunk_01", key=f"chunk_{book_id}"):
            st.warning("TODO: plugar aqui run_chunk_01(book_id, ...).")


# -------------------------------------------------------------------
# PÁGINA 3 — DIAGNOSTICS
# -------------------------------------------------------------------

def page_diagnostics():
    st.header("3 — Diagnostics (read-only)")

    rows = get_books_basic()
    if not rows:
        st.info("Nenhum livro cadastrado.")
        return

    for r in rows:
        book_id, title, seal, author_original = r
        split_count = get_split_count(book_id)
        chunk_count = get_chunk_count(book_id)

        st.markdown(f"### {book_id:04d} — {title}")
        st.write(f"- **Seal:** {seal}")
        st.write(f"- **Author:** {author_original}")
        st.write(f"- **Normalize:** {'✅ OK' if has_normalized(book_id) else '❌ NO'}")
        st.write(
            f"- **Split:** {'✅ OK' if split_count > 0 else '❌ NO'} "
            f"(items: {split_count})"
        )
        st.write(
            f"- **Chunks:** {'✅ OK' if chunk_count > 0 else '❌ NO'} "
            f"(items: {chunk_count})"
        )
        st.markdown("---")


# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------

def main():
    st.set_page_config(page_title="Gaiden BookMaker", layout="wide")
    st.title("Gaiden BookMaker")

    tab_debug, tab_books, tab_pipeline, tab_diag, tab_keys = st.tabs(
        ["Debug", "Books & Templates", "Pipeline", "Diagnostics", "API Key"]
    )

    with tab_debug:
        page_debug_books()
    with tab_books:
        page_books_templates()
    with tab_pipeline:
        page_pipeline()
    with tab_diag:
        page_diagnostics()
    with tab_keys:
        page_key_manager()


if __name__ == "__main__":
    main()
