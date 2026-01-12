#!/usr/bin/env bash

set -e

#############################################
# 0. Ir para o repositório do Gaiden
#############################################

cd ~/Projetos/gaiden_bookmaker

echo "[INFO] Diretório atual: $PWD"

#############################################
# 1. Backup dos contratos atuais
#############################################

BK_DIR="backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BK_DIR/contracts"

echo "[INFO] Fazendo backup dos contratos em $BK_DIR/contracts"

cp gaiden/contracts/en_modern_2025.json "$BK_DIR/contracts/" 2>/dev/null || true
cp gaiden/contracts/en_de_krimi_2025.json "$BK_DIR/contracts/" 2>/dev/null || true
cp gaiden/contracts/en_ptbr_2025.json "$BK_DIR/contracts/" 2>/dev/null || true
cp gaiden/contracts/en_es_2025.json "$BK_DIR/contracts/" 2>/dev/null || true

ls -lah "$BK_DIR/contracts"

#############################################
# 2. Normalizar / enriquecer os 4 contratos 2025 com jq
#    - adiciona target_language, language_code, model,
#      temperature, max_output_tokens, se ainda não existirem
#############################################

echo "[INFO] Enriquecendo contratos com campos padrão..."

# EN moderno (EN -> EN moderno)
if [ -f gaiden/contracts/en_modern_2025.json ]; then
  jq '
    .target_language //= "en" |
    .language_code   //= "en" |
    .model           //= "gpt-5.1" |
    .temperature     //= 0.4 |
    .max_output_tokens //= 1200
  ' gaiden/contracts/en_modern_2025.json > /tmp/en_modern_2025.json && \
  mv /tmp/en_modern_2025.json gaiden/contracts/en_modern_2025.json
fi

# EN -> PT-BR
if [ -f gaiden/contracts/en_ptbr_2025.json ]; then
  jq '
    .target_language //= "pt-BR" |
    .language_code   //= "pt-BR" |
    .model           //= "gpt-5.1" |
    .temperature     //= 0.4 |
    .max_output_tokens //= 1200
  ' gaiden/contracts/en_ptbr_2025.json > /tmp/en_ptbr_2025.json && \
  mv /tmp/en_ptbr_2025.json gaiden/contracts/en_ptbr_2025.json
fi

# EN -> ES
if [ -f gaiden/contracts/en_es_2025.json ]; then
  jq '
    .target_language //= "es" |
    .language_code   //= "es" |
    .model           //= "gpt-5.1" |
    .temperature     //= 0.4 |
    .max_output_tokens //= 1200
  ' gaiden/contracts/en_es_2025.json > /tmp/en_es_2025.json && \
  mv /tmp/en_es_2025.json gaiden/contracts/en_es_2025.json
fi

# EN -> DE
if [ -f gaiden/contracts/en_de_krimi_2025.json ]; then
  jq '
    .target_language //= "de" |
    .language_code   //= "de" |
    .model           //= "gpt-5.2" |
    .temperature     //= 0.4 |
    .max_output_tokens //= 1200
  ' gaiden/contracts/en_de_krimi_2025.json > /tmp/en_de_krimi_2025.json && \
  mv /tmp/en_de_krimi_2025.json gaiden/contracts/en_de_krimi_2025.json
fi

echo "[INFO] Contratos atualizados (sem mexer no texto dos prompts)."

#############################################
# 3. (COMENTADO) Ajustes sugeridos em gaiden/translate.py
#    -> ISSO VOCÊ FAZ NO EDITOR (code/nano), SÓ TO DOCUMENTANDO AQUI
#############################################

cat << 'EOF'

[TODO MANUAL EM translate.py – abrir no editor]

1) Criar uma função utilitária para deduzir idioma-alvo:

    def _get_target_language(contract: dict) -> str:
        return (
            contract.get("target_lang")
            or contract.get("target_language")
            or contract.get("language_code")
            or contract.get("output", {}).get("language")
            or "en"
        )

2) Ajustar o _call_openai_translate (ou equivalente) para usar o modelo do contrato:

    def _call_openai_translate(prompt: str, contract: dict) -> str:
        model = contract.get("model", "gpt-5.1")
        temperature = contract.get("temperature", 0.4)
        max_output_tokens = contract.get("max_output_tokens", 1200)

        response = client.responses.create(
            model=model,
            input=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        return response.output[0].content[0].text

3) No loop principal de tradução em translate.py:

    - carregar o contrato JSON (já contendo target_language/model/etc.)
    - usar _get_target_language(contract) para montar o prompt
    - passar o próprio contract para _call_openai_translate(prompt, contract)
    - salvar no DB incluindo language_code/target_language (se a tabela tiver esse campo)

Para editar:

    code gaiden/translate.py
ou
    nano gaiden/translate.py

EOF

#############################################
# 4. Criar scripts finos por língua (wrappers)
#    Estes scripts vão chamar uma função genérica
#    que você implementa em translate.py, ex:
#    run_translate_with_contract(contract_path)
#############################################

echo "[INFO] Criando wrappers de tradução por língua em gaiden/ ..."

mkdir -p gaiden

# EN moderno (EN -> EN moderno)
cat > gaiden/translate_en_2025.py << 'EOF'
from gaiden.translate import run_translate_with_contract

def main():
    run_translate_with_contract("gaiden/contracts/en_modern_2025.json")

if __name__ == "__main__":
    main()
EOF

# EN -> PT-BR
cat > gaiden/translate_ptbr_2025.py << 'EOF'
from gaiden.translate import run_translate_with_contract

def main():
    run_translate_with_contract("gaiden/contracts/en_ptbr_2025.json")

if __name__ == "__main__":
    main()
EOF

# EN -> ES
cat > gaiden/translate_es_2025.py << 'EOF'
from gaiden.translate import run_translate_with_contract

def main():
    run_translate_with_contract("gaiden/contracts/en_es_2025.json")

if __name__ == "__main__":
    main()
EOF

# EN -> DE
cat > gaiden/translate_de_2025.py << 'EOF'
from gaiden.translate import run_translate_with_contract

def main():
    run_translate_with_contract("gaiden/contracts/en_de_krimi_2025.json")

if __name__ == "__main__":
    main()
EOF

echo "[INFO] Wrappers criados:"
ls -1 gaiden/translate_*2025.py

cat << 'EOF'

[TODO MANUAL EM gaiden/translate.py – implementar run_translate_with_contract]

Exemplo de assinatura sugerida:

    def run_translate_with_contract(contract_path: str) -> None:
        contract = _load_contract(contract_path)
        target_lang = _get_target_language(contract)
        split_items = _fetch_split_items()  # book_split_items -> path dos chunks
        for item in split_items:
            chunk_text = _load_chunk_text(item)
            prompt = build_prompt(chunk_text, contract)
            translated = _call_openai_translate(prompt, contract)
            _save_translation_db(item, translated, contract, target_lang)

Depois disso, para testar EN moderno:

    source .venv/bin/activate   # se estiver usando venv
    cd ~/Projetos/gaiden_bookmaker
    python -m gaiden.translate_en_2025

Para PT-BR:

    python -m gaiden.translate_ptbr_2025

E assim sucessivamente.

EOF

echo "[INFO] Fim do script bash. Ajustes em Python devem ser feitos agora no editor."
