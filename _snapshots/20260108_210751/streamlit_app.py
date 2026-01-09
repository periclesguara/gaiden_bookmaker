from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List

import streamlit as st

from gaiden.db import (
    init_db,
    insert_book,
    insert_file,
    upsert_extracted_text,
    upsert_normalized_text,
    update_book_about,
)
from gaiden.ingest import save_upload, extract_text_from_file, ALLOWED_EXT
from gaiden.normalize import normalize_text_v2
from gaiden.diagnostics import get_book_diagnostics
from gaiden.templates import copyright_page
from gaiden.about import about_contributor_block, about_edition_block

DB_PATH = Path("data/db/gaiden.sqlite3")


def load_books_rows():
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(DB_PATH.as_posix())
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
              id,
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
              COALESCE(about_work, '') AS about_work,
              COALESCE(about_contributor, '') AS about_contributor
            FROM books
            ORDER BY id ASC
            """
        ).fetchall()
        return rows
    finally:
        conn.close()


def load_normalized_text(book_id: int) -> str | None:
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH.as_posix())
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT normalized_text FROM book_normalized WHERE book_id = ?",
            (book_id,),
        ).fetchone()
        if row and row["normalized_text"]:
            return row["normalized_text"]
        return None
    finally:
        conn.close()


def frontispiece_text(row) -> str:
    title = row["title"]
    author = row["author_original"]
    collaborator = row["collaborator_name"] or ""
    roles_raw = row["collaborator_roles"] or "[]"
    imprint = row["seal"]
    parent = row["imprint_parent"] or ""
    # garantimos string para não quebrar no join
    place = str(row["place"] or "")
    year = str(row["year"] or "")
    try:
        roles_list: List[str] = json.loads(roles_raw) if roles_raw else []
    except Exception:
        roles_list = []
    main_role = roles_list[0] if roles_list else "collaborator"

    lines = []
    lines.append(title)
    lines.append(f"by {author}")
    lines.append("")
    if collaborator:
        role_label = main_role.capitalize()
        lines.append(f"{role_label}: {collaborator}")
        lines.append("")
    imprint_line = imprint
    if parent:
        imprint_line = f"{imprint} (a {parent} imprint)"
    lines.append(imprint_line)
    if place or year:
        place_year = ", ".join([p for p in [place, year] if p])
        if place_year:
            lines.append(place_year)
    return "\n".join(lines).strip()


def build_copyright_preview(row, about_work: str, about_contributor: str) -> str:
    """
    Usa SEU template de copyright (gaiden.templates.copyright_page)
    + SEUS blocks multilíngues de About (gaiden.about).
    """
    roles_raw = row["collaborator_roles"] or "[]"
    try:
        roles_list: List[str] = json.loads(roles_raw) if roles_raw else []
    except Exception:
        roles_list = []

    lang = (row["lang"] or "en").strip().lower()
    contributor_name = row["collaborator_name"] or ""

    base = copyright_page(
        title=row["title"],
        author_original=row["author_original"],
        contributor_name=contributor_name,
        contributor_roles=roles_list,
        imprint=row["seal"],
        imprint_parent=row["imprint_parent"] or "",
        place=row["place"] or "",
        year=row["year"] or "2026",
        lang=lang,
        public_domain_origin=bool(row["public_domain_origin"]),
    )

    blocks: List[str] = [base]

    # About Edition
    about_work_text = about_work or ""
    edition_block = about_edition_block(
        language_code=lang,
        custom_text=about_work_text if about_work_text.strip() else None,
    )
    blocks.append(edition_block.strip())

    # About Contributor (se tiver colaborador)
    if contributor_name.strip():
        about_contrib_text = about_contributor or ""
        contrib_block = about_contributor_block(
            contributor_name=contributor_name,
            contributor_role_codes=roles_list,
            language_code=lang,
            custom_text=about_contrib_text if about_contrib_text.strip() else None,
        )
        blocks.append(contrib_block.strip())

    return "\n\n".join(b for b in blocks if b).strip()


def page_book_and_copyright():
    st.header("1 — Book & Copyright")

    st.subheader("1.1 — New book (index + upload)")

    with st.form("new_book_form"):
        c1, c2 = st.columns(2)
        with c1:
            seal = st.text_input("Imprint / Seal", value="Manta Quest")
            imprint_parent = st.text_input("Parent imprint / House", value="RinoBooks")
            lang = st.selectbox("Language", ["en", "es", "pt"], index=0)
        with c2:
            title = st.text_input("Title")
            author_original = st.text_input("Original author")
            collaborator_name = st.text_input("Collaborator name / pseudonym", value="")
        roles = st.multiselect(
            "Collaborator roles",
            options=["adapter", "translator", "curator", "editor", "reviewer"],
            default=["adapter"],
        )
        place = st.text_input("Place", value="Rio de Janeiro - Brasil")
        year = st.text_input("Year", value="2026")
        public_domain_origin = st.checkbox(
            "Check if original work is public domain", value=True
        )

        uploaded = st.file_uploader(
            "Upload source file (txt/md/pdf/docx)",
            type=list(ALLOWED_EXT),
        )

        submit = st.form_submit_button("Save & index book")

    if submit:
        if not uploaded:
            st.error("Please upload a file.")
            return
        if not title or not author_original or not seal:
            st.error("Title, original author and seal are required.")
            return

        data = uploaded.read()
        stored_path, ext, digest = save_upload(data, uploaded.name)
        size_bytes = len(data)
        roles_json = json.dumps(roles, ensure_ascii=False)

        book_id = insert_book(
            seal=seal,
            title=title,
            author_original=author_original,
            collaborator_name=collaborator_name,
            collaborator_roles=roles_json,
            imprint_parent=imprint_parent,
            lang=lang,
            place=place,
            year=year,
            public_domain_origin=1 if public_domain_origin else 0,
        )
        insert_file(
            book_id=book_id,
            original_filename=uploaded.name,
            ext=ext,
            sha256=digest,
            stored_path=str(stored_path),
            mime_type="text/plain",
            size_bytes=size_bytes,
        )
        extracted = extract_text_from_file(stored_path, ext) or ""
        upsert_extracted_text(book_id, extracted)

        if extracted:
            norm = normalize_text_v2(extracted)
            upsert_normalized_text(book_id, norm, version="v2")

        st.success(f"Saved successfully! book_id={book_id}")

    st.markdown("---")
    st.subheader("1.2 — Existing books, frontispiece & copyright preview")

    books = load_books_rows()
    if not books:
        st.info("No books indexed yet.")
        return

    st.dataframe(
        [
            {
                "id": r["id"],
                "seal": r["seal"],
                "title": r["title"],
                "author": r["author_original"],
                "collaborator": r["collaborator_name"],
                "lang": r["lang"],
            }
            for r in books
        ],
        use_container_width=True,
        hide_index=True,
    )

    ids = [r["id"] for r in books]
    selected_id = st.selectbox(
        "Select book",
        options=ids,
        index=0,
        format_func=lambda v: f"{v:04d} — {next(r['title'] for r in books if r['id']==v)}",
    )

    row = next(r for r in books if r["id"] == selected_id)

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("### Frontispiece / Title page")
        st.code(frontispiece_text(row), language="markdown")

        st.markdown("### About sections (optional / overrides defaults)")
        about_work = st.text_area(
            "About this work / edition",
            value=row["about_work"] or "",
            height=5 * 24,
        )
        about_contrib = st.text_area(
            "About the collaborator / adapter",
            value=row["about_contributor"] or "",
            height=5 * 24,
        )
        if st.button("Save about sections"):
            update_book_about(selected_id, about_work, about_contrib)
            st.success("About sections updated.")

    with c2:
        st.markdown("### Copyright page (preview)")
        cpp = build_copyright_preview(row, about_work, about_contrib)
        st.code(cpp, language="markdown")

        st.markdown("### Normalize preview (sample)")
        norm_txt = load_normalized_text(selected_id)
        if norm_txt:
            st.text_area(
                "Normalized text (first 1500 chars)",
                value=norm_txt[:1500],
                height=12 * 24,
            )
        else:
            st.info("No normalized text stored for this book yet.")


def page_pipeline():
    st.header("2 — Pipeline (split & chunks)")

    books = load_books_rows()
    if not books:
        st.info("No books indexed yet.")
        return

    ids = [r["id"] for r in books]
    selected_id = st.selectbox(
        "Select book",
        options=ids,
        format_func=lambda v: f"{v:04d} — {next(r['title'] for r in books if r['id']==v)}",
    )

    diag = get_book_diagnostics(selected_id)

    st.markdown("### 01 — SPLIT (Structure only)")
    if diag.split_ok:
        st.success(f"SPLIT: OK — {diag.split_units} units")
    else:
        st.warning("SPLIT: NOT YET")

    if diag.split_ok:
        conn = sqlite3.connect(DB_PATH.as_posix())
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT item_index, item_type, label, token_count, char_count, path
                FROM book_split_items
                WHERE book_id = ?
                ORDER BY item_index ASC
                """,
                (selected_id,),
            ).fetchall()
        finally:
            conn.close()

        st.markdown("**Structure units preview**")
        st.dataframe(
            [
                {
                    "idx": r["item_index"],
                    "type": r["item_type"],
                    "label": r["label"],
                    "tokens": r["token_count"],
                    "chars": r["char_count"],
                    "path": r["path"],
                }
                for r in rows
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("---")
    st.markdown("### 02 — CHUNK (Token chunks only)")

    if diag.chunk_ok:
        st.success(f"CHUNK: OK — {diag.chunk_count} chunks")
    else:
        st.warning("CHUNK: NOT YET")

    if diag.chunk_ok:
        chunk_index = st.number_input(
            "Open chunk index", min_value=1, max_value=diag.chunk_count, value=1, step=1
        )
        chunks_dir = Path("data/chunks") / f"book_{selected_id:04d}" / "split_01"
        chunk_path = chunks_dir / f"{int(chunk_index):04d}.txt"
        if chunk_path.exists():
            txt = chunk_path.read_text(encoding="utf-8", errors="replace")
            st.text_area(
                "Chunk text",
                value=txt,
                height=18 * 24,
            )
        else:
            st.error(f"Chunk file not found: {chunk_path}")


def page_diagnostics():
    st.header("3 — Diagnostics")

    books = load_books_rows()
    if not books:
        st.info("No books indexed yet.")
        return

    ids = [r["id"] for r in books]
    selected_id = st.selectbox(
        "Select book",
        options=ids,
        format_func=lambda v: f"{v:04d} — {next(r['title'] for r in books if r['id']==v)}",
    )

    diag = get_book_diagnostics(selected_id)

    st.markdown(f"### Book {selected_id:04d}")
    st.markdown(f"- **Indexed**: `{diag.indexed_ok}`")
    st.markdown(f"- **File exists**: `{diag.file_ok}` ({diag.file_path})")
    st.markdown(f"- **Extracted text**: `{diag.extracted_ok}`")
    st.markdown(f"- **Normalized text**: `{diag.normalized_ok}`")
    st.markdown(f"- **Split units**: `{diag.split_ok}` ({diag.split_units})")
    st.markdown(f"- **Chunks**: `{diag.chunk_ok}` ({diag.chunk_count})")


def main():
    init_db()

    st.set_page_config(
        page_title="Gaiden BookMaker",
        page_icon="📚",
        layout="wide",
    )

    st.sidebar.title("Gaiden BookMaker")
    page = st.sidebar.radio(
        "Go to",
        ["Book & Copyright", "Pipeline", "Diagnostics"],
        index=0,
    )

    if page == "Book & Copyright":
        page_book_and_copyright()
    elif page == "Pipeline":
        page_pipeline()
    else:
        page_diagnostics()


if __name__ == "__main__":
    main()
