from __future__ import annotations

from pathlib import Path

import streamlit as st

from gaiden.merge_translated import (
    list_languages_for_book,
    merge_language,
    BASE_DIR,
)


def _default_book_id() -> int:
    # no futuro podemos puxar isso do DB; por enquanto, default = 1
    return 1


def main() -> None:
    st.set_page_config(page_title="Gaiden – Merge Translated", page_icon="📚")

    st.title("03 – Merge Translated Chunks")
    st.write(
        "Unifica os chunks traduzidos (EN / PT-BR / ES / etc.) "
        "em arquivos únicos por idioma, prontos para refino e exportação."
    )

    # para não depender do DB ainda, deixamos o book_id como input manual
    book_id = st.number_input(
        "Book ID",
        min_value=1,
        max_value=9999,
        value=_default_book_id(),
        step=1,
        help="ID lógico do livro (ex.: 1 -> book_0001).",
    )

    book_dir = BASE_DIR / "data" / "translated" / f"book_{book_id:04d}"
    if not book_dir.is_dir():
        st.warning(f"Nenhum diretório encontrado em {book_dir}. "
                   f"Verifique se o pipeline de tradução rodou para esse book_id.")
        return

    langs = list_languages_for_book(book_id)
    if not langs:
        st.info("Nenhum idioma encontrado em data/translated para este book_id.")
        return

    # filtro de idiomas (evita selecionar diretórios 'merged_*' sem querer)
    langs = [l for l in langs if not l.startswith("merged_")]

    st.subheader("Idiomas disponíveis")
    st.write("Diretórios encontrados:")
    for lang in langs:
        st.write(f"- `{lang}`  →  `{(book_dir / lang).relative_to(BASE_DIR)}`")

    selected_langs = st.multiselect(
        "Selecione os idiomas para fazer merge",
        options=langs,
        default=langs,
    )

    if not selected_langs:
        st.info("Selecione pelo menos um idioma para prosseguir.")
        return

    if st.button("🚀 Rodar merge para idiomas selecionados"):
        st.write("Executando merge…")
        out_paths = []
        for lang in selected_langs:
            try:
                out_path = merge_language(book_id, lang)
                if out_path:
                    out_paths.append(out_path)
                else:
                    st.warning(f"Nenhum arquivo para mesclar em {lang}.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Erro ao mesclar {lang}: {exc}")
        if out_paths:
            st.success("Merge concluído.")
            st.subheader("Arquivos gerados")
            for p in out_paths:
                st.code(str(p.relative_to(BASE_DIR)), language="bash")

    st.markdown("---")
    st.caption(
        "Observação: o merge grava o arquivo unificado no diretório do idioma e "
        "registra o texto no banco para revisão e análise."
    )


if __name__ == "__main__":
    main()
