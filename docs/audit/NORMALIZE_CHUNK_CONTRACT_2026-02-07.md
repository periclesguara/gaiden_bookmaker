# Normalize + Chunk Contract (2026-02-07)

## Canonical Paths
- RAW: `data/raw/<book_code>/<LANG>/source.txt`
- NORMALIZED: `data/normalized/<book_code>/<LANG>/<book_code>_<lang_lower>_v2.txt`
- NORMALIZE report: `data/normalized/<book_code>/<LANG>/normalize_report.json`
- NORMALIZE preview: `data/normalized/<book_code>/<LANG>/normalize_preview.txt`
- CHUNKS dir: `data/chunks/<book_code>/<lang_lower>/`
- CHUNKS manifest: `data/chunks/<book_code>/<lang_lower>/chunks_manifest.json`

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

## Chunk Check (OK/FAIL)
- FAIL se qualquer chunk > max_chars.
- FAIL se qualquer chunk contém heading de outro capítulo.
- OK caso contrário.
