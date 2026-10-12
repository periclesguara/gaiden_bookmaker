# Architecture Diagnostic

Date: 2026-04-08

## Duplicidade estrutural

- Root canônico de artefatos já existente: `data/`
- Root paralelo/deprecado: `web/data/`
- Evidência atual em `web/data`: `README.md`, `db/gaiden.sqlite3`

## Hardcodes críticos encontrados

- Paths absolutos de repo root em scripts Sherlock de build
- Resolução manual repetida de `Path(settings.BASE_DIR).parent / "data"` em views/serviços web
- Loader direto de `.gaiden_secrets` em `gaiden/secrets.py`

## Legacy

- `legacy/gaiden/split_merge_translate_for_refine.py` existe, mas não há imports ativos dele no código pesquisado
- `web/pipeline/services/legacy_merges.py` atua como ponte de compatibilidade para nomes/caminhos antigos

## Scripts

- Build: 3
- Ops/migration: 2
- Debug/one-shot: 2

## Decisão proposta

- Consolidar `data/` como único storage root canônico
- Tratar `web/data/` como legado/deprecado
- Centralizar resolução de path, segredos, gates e status antes de avançar na limpeza maior
