# Gaiden BookMaker — Log Técnico Consolidado

**Escopo:** B (log completo)  
**Formato:** MD técnico  
**Ordem:** Steps incrementais (C)  
**Modo:** v1 (seca)  
**log_version:** 1.0.0  
**pipeline_version:** 2026.01  
**contract_version:** en_polish_2025.01  
**sha256_input:**  
**sha256_output:**  
**input_tokens:**  
**output_tokens:**  
**model_cost:**  
**latency_ms:**  
**status:** success  
**stage:** polish  
**replayable:** true

## 1) Banco de Dados (estado atual)
- Base operando com `book_translated_merged` como fonte para refine/polish.
- Campos relevantes: `book_id`, `lang_key`, `merged_text`, `merged_path`, `chunk_count`.
- Observações: `en_modern_2025` como base do polish; `es_2025` e `ptbr_2025` presentes.

## 2) Módulo `gaiden/db.py`
- Helpers para leitura de `book_translated_merged`.
- Registro de `book_polished_merged` compatível com schema real.
- Schema real de `book_polished_merged`: `book_id`, `lang`, `variant`, `source_kind`, `source_path`, `polished_path`, `model`, `created_at`.
- Decisão: não armazenar `polished_text` no DB.

## 3) Contratos — polish
- Contrato em `gaiden/contracts/polish/en_polish_2025.json`.
- Modelo: `gpt-4.o`.
- Regras rígidas: preservação de markers `@@PXXXX@@`, proibição de split/merge de parágrafos.

## 4) Pipeline — polish EN 2025
- Arquivo: `gaiden/polish_en_2025.py`.
- Fluxo: carregar merged via DB → inserir markers → Responses API → validar estrutura → remover markers → salvar → registrar no DB.
- Responses API: `type = "input_text"`.

## 5) Correções de API
- Corrigido `type="text"` para `type="input_text"`.
- Substituído client global por `get_client()`.
- Corrigidos f-strings com `\n` dentro de expressão.

## 6) Proteção estrutural — markers
- Colapso real detectado: 1993 → 131 parágrafos.
- Solução: markers `@@PXXXX@@` com validação 1:1.

## 7) Output — polish
- Arquivo: `merged_polished_en_2025.txt`.
- Registro DB: `lang=en_modern_2025`, `variant=polish_2025`, `source_kind=translated_merged`, `source_path`, `polished_path`, `model=gpt-4.o`.

## 8) Django — ativação pipeline
- `pipeline` registrado em `INSTALLED_APPS`.
- Rota `/pipeline/` direta.
- Integração DB → UI.

## 9) Dashboard — status
- Consulta baseada em `book_translated_merged` e `book_polished_merged`.
- Tabela: idioma × estágio (Translated/Refined/Polished) com `✅`/`—`.

## 10) Teste operacional
- `curl http://localhost:8000/pipeline/` retorna 200 OK.

---

## Checklist — Estado Operacional
- DB operacional.
- Pipeline polish com markers.
- Responses API integrada.
- Django ativo.
- Dashboard funcional.

---

## Próximos passos (arquitetura)
- Fase 1: refine/translate + cadastro de livro + seleção de idiomas.
- Fase 2: scheduler (Celery + Redis), retries, logs.
- Fase 3: export (EPUB3, MOBI/AZW3, KDP).
- Fase 4: catálogo (Sherlock I/II, multi-book, outros gêneros).

---

## Plano KDP — pós-pipeline
- Metadados, capa, blurbs, keywords, pricing, KU.

---

## Lições aprendidas
- Responses API exige `input_text`.
- Polish precisa de markers para preservar estrutura.
- DB deve armazenar paths, não texto grande.
- Dashboard mínimo já dá visibilidade operacional.
