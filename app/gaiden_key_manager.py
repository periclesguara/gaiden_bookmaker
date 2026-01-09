from __future__ import annotations

import streamlit as st

from gaiden.secrets import get_openai_key, set_openai_key


def main():
    st.set_page_config(page_title="Gaiden – API Key Manager", page_icon="🔐")

    st.title("🔐 Gaiden – OpenAI API Key")
    st.write(
        "Cole aqui sua chave da OpenAI. "
        "Ela será salva localmente em um arquivo oculto (.gaiden_secrets) "
        "no diretório do projeto, e **não** será enviada para o Git."
    )

    current = get_openai_key() or ""
    masked = "***************" if current else "(none)"

    st.markdown(f"**Status atual:** `{masked}`")

    new_key = st.text_input(
        "OPENAI_API_KEY",
        value=current,
        type="password",
        help="Cole aqui sua chave começando com 'sk-...'.",
    )

    col1, col2 = st.columns(2)

    with col1:
        if st.button("💾 Save key"):
            if not new_key.strip():
                st.error("Chave vazia. Cole uma chave válida.")
            else:
                set_openai_key(new_key.strip())
                st.success("Chave salva com sucesso!")

    with col2:
        if st.button("❌ Clear key"):
            set_openai_key("")
            st.warning("Chave removida. O tradutor não funcionará até configurar outra.")

    st.info(
        "Os módulos de tradução do Gaiden (como `translate_en_modern`) "
        "vão usar automaticamente essa chave."
    )


if __name__ == "__main__":
    main()
