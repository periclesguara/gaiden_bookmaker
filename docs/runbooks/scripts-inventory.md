# Scripts Inventory

Date: 2026-04-08

## Wrapper / compatibilidade

- `scripts/build_sherlock_md_final.py`
- `scripts/build_sherlock_md_final_es.py`
- `scripts/build_sherlock_md_final_ptbr.py`

## Migration

- `scripts/migrate_sqlite_books_to_postgres.py`

## Wrapper operacional

- `scripts/open_gaiden`

## One-off

- `scripts/normalize_sherlock_adventures_en.py`

## Deprecated candidate

- `scripts/polish_merge_refine_clean.py`

## Notes

- Os scripts de build Sherlock ainda existem por compatibilidade operacional.
- Hardcodes absolutos de repo root foram removidos nesta fase.
- A CLI oficial inicial agora existe em `gaiden.interfaces.cli`.
- A CLI oficial da Collection agora existe em `gaiden.interfaces.collections_cli`.
- Script sem classificação não deve virar caminho oficial.
