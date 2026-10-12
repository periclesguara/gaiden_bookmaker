# Legacy Usage

Date: 2026-04-08

## Imports ativos

- Não foram encontrados imports ativos de `legacy/gaiden` no código pesquisado

## Artefatos legados relevantes

- `legacy/gaiden/split_merge_translate_for_refine.py`
- `web/pipeline/services/legacy_merges.py` funciona como ponte compatível para merges antigos

## Classificação

- `legacy/gaiden/*`: passivo legado controlado
- `web/pipeline/services/legacy_merges.py`: compat layer ativa

## Regra

- código novo não deve importar `legacy/`
- futura remoção do arquivo em `legacy/gaiden` depende apenas de confirmação operacional
