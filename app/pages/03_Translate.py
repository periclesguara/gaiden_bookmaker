from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from gaiden.translate import run_translate_with_contract


# BASE_DIR = raiz do projeto (onde está pyproject.toml)
BASE_DIR = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = BASE_DIR / "gaiden" / "contracts"


def _load_contract_meta(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "name": path.name,
            "model": "?",
            "output_language": "?",
            "raw": {},
        }

    lang = (
        data.get("output", {}).get("language")
        or data.get("target_language")
        or data.get("target_lang")
        or "?"
    )

    return {
        "name": data.get("name") or path.name,
        "model": data.get("model") or "gpt-5.1",
        "output_language": lang,
        "raw": data,
    }


def main() -> None:
    st.title("🔁 Gaiden Translate 2025")

    st.write(
        """
Interface para rodar **traduções por contrato** usando o pipeline file-based:

- lê chunks `.txt` de um diretório (`chunk_dir`)
- usa o contrato JSON em `gaiden/contracts/*.json`
- grava os chunks traduzidos em `out_dir`
        """
    )

    if not CONTRACTS_DIR.is_dir():
        st.error(f"Diretório de contratos não encontrado: `{CONTRACTS_DIR}`")
        return

    json_files = sorted(CONTRACTS_DIR.glob("*.json"))
    if not json_files:
        st.warning("Nenhum contrato `.json` encontrado em `gaiden/contracts`.")
        return

    metas = [_load_contract_meta(p) for p in json_files]

    # labels bonitinhas
    options = []
    for path, meta in zip(json_files, metas):
        label = f"{meta['name']}  |  {meta['output_language']}  |  {meta['model']}  ({path.name})"
        options.append(label)

    st.subheader("Selecionar contrato de tradução")

    # tenta selecionar EN moderno como default
    default_index = 0
    for i, p in enumerate(json_files):
        if "en_modern_2025" in p.name:
            default_index = i
            break

    choice = st.selectbox(
        "Contrato",
        options=options,
        index=default_index,
    )

    selected_idx = options.index(choice)
    selected_path = json_files[selected_idx]
    selected_meta = metas[selected_idx]

    st.markdown("### Detalhes do contrato selecionado")
    with st.expander("Ver JSON do contrato"):
        st.code(
            json.dumps(selected_meta["raw"], indent=2, ensure_ascii=False),
            language="json",
        )

    if st.button("🚀 Rodar tradução para este contrato"):
        st.info(f"Iniciando tradução com `{selected_path}`...")
        try:
            with st.spinner("Chamando OpenAI e gravando chunks traduzidos..."):
                run_translate_with_contract(str(selected_path))
            st.success(
                "✅ Tradução concluída. Verifique os arquivos no diretório `out_dir` definido no contrato."
            )
        except Exception as e:
            st.error(f"❌ Erro ao rodar tradução: {e!r}")


if __name__ == "__main__":
    main()
