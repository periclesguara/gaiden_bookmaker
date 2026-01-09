import streamlit as st
import pandas as pd
from pathlib import Path

from gaiden.units_db import get_split_status, list_units
from gaiden.chunks_db import get_split01_status, list_split01_chunks
from gaiden.split_struct import run_split_struct
from gaiden.chunk_01 import run_chunk_01

st.set_page_config(page_title="Split & Chunk", layout="wide")
st.title("Split (Structure) + Chunk (Token)")

book_id = st.number_input("Book ID", min_value=1, value=1, step=1)

col1, col2 = st.columns(2, gap="large")

with col1:
    st.subheader("01 — SPLIT (Structure only)")
    s = get_split_status(book_id)
    if not s.get("exists"):
        st.info("SPLIT: NOT YET")
    else:
        st.success(f"SPLIT: OK — {s['count']} units")
        st.caption(f"method={s['method']} | at {s['created_at']}")

    if st.button("Run SPLIT (structure)", type="primary"):
        with st.spinner("Detecting structure..."):
            n = run_split_struct(int(book_id))
        st.success(f"Done! Units detected: {n}")
        st.rerun()

    st.divider()
    st.subheader("Structure units preview")
    units = list_units(book_id, limit=200)
    if units:
        st.dataframe(pd.DataFrame(units), use_container_width=True)
    else:
        st.caption("No units yet.")

with col2:
    st.subheader("02 — CHUNK (Token chunks only)")
    c = get_split01_status(book_id)
    if not c.get("exists"):
        st.info("CHUNK: NOT YET")
    else:
        st.success(f"CHUNK: OK — {c['count']} chunks")
        st.caption(
            f"lang={c['language']} | min={c['min_tokens']} target={c['target_tokens']} max={c['max_tokens']} | at {c['created_at']}"
        )

    min_t = st.number_input("min_tokens", value=1500, step=50)
    tgt_t = st.number_input("target_tokens", value=1800, step=50)
    max_t = st.number_input("max_tokens", value=2200, step=50)

    if st.button("Run CHUNK (split_01)", type="primary"):
        with st.spinner("Chunking text (requires SPLIT)..."):
            n = run_chunk_01(int(book_id), min_tokens=int(min_t), target_tokens=int(tgt_t), max_tokens=int(max_t))
        st.success(f"Done! Chunks created: {n}")
        st.rerun()

    st.divider()
    st.subheader("Chunks preview")
    rows = list_split01_chunks(book_id, limit=120)
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True)

        st.subheader("Open chunk")
        idx = st.number_input("chunk_index", min_value=1, value=int(df.iloc[0]["chunk_index"]))
        match = [r for r in rows if r["chunk_index"] == idx]
        if match:
            p = Path(match[0]["out_path"])
            if p.exists():
                st.text_area("Chunk text", p.read_text(encoding="utf-8", errors="replace")[:20000], height=380)
            else:
                st.error(f"File not found: {p}")
    else:
        st.caption("No chunks yet.")
