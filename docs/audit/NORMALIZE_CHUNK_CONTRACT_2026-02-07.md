# Normalize + Chunk Contract (2026-02-07)

## Canonical Paths
- RAW: `data/raw/<book_code>/<lang>/source.(txt|md)`
- NORMALIZED: `data/normalized/<book_code>/<lang>/<book_code>_<lang>_v2.txt`
- NORMALIZE report: `data/normalized/<book_code>/<lang>/normalize_report.json`
- NORMALIZE preview: `data/normalized/<book_code>/<lang>/normalize_preview.txt`
- CHUNKS dir: `data/chunks/<book_code>/<lang_lower>/`
- CHUNKS manifest: `data/chunks/<book_code>/<lang_lower>/chunks_manifest.json`

## Language Canonical Form
- Sempre lower-case no filesystem: `en`, `es`, `fr`, `it`, `de`, `ptbr`.
- Compatibilidade de leitura: RAW pode existir em `EN`/`PT-BR` etc., mas escrita sempre em lower-case.

## Gutenberg Removal
- Remover blocos `START OF THIS PROJECT GUTENBERG EBOOK` e `END OF THIS PROJECT GUTENBERG EBOOK`.
- Remover seção `START: FULL LICENSE` / `PROJECT GUTENBERG LICENSE` e variantes.
- Remover `Produced by ...` e `This file was produced by ...` quando estiver dentro do bloco de licença.
- Normalized deve manter apenas headings/chapters/index (se houver) + corpo do texto.

## Chunk Rules
- Um chunk nunca atravessa capítulos (um chunk pertence a um capítulo).
- Tamanho-alvo: ~1500 tokens.
- Hard cap: 1500 tokens.
- Heurística: 1 token ≈ 4 caracteres.
- Target chars: 5200–5800 (default 5600).
- Max chars: 6000.

## Normalize Check (OK/FAIL)
- FAIL se normalized vazio.
- FAIL se normalized < 20% do raw.
- FAIL se ainda contém `PROJECT GUTENBERG` / `FULL LICENSE` / `START OF ...` / `END OF ...`.
- OK caso contrário.

## Normalize Report (normalize_report.json)
- `input_path`
- `output_path`
- `chapters_detected`
- `boilerplate_removed`
- `status` (OK|FAIL)

## Chunk Check (OK/FAIL)
- FAIL se qualquer chunk > max_chars.
- FAIL se qualquer chunk contém heading de outro capítulo.
- OK caso contrário.
