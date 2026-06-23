# ADR: Storage Canonico em data/

## Status

Aceito.

## Decisao

`data/` na raiz do repositorio e o storage fisico canonico do Gaiden/RinoBooks.

`web/data` nao e storage operacional oficial e deve permanecer removido ou arquivado como passivo legado em `backups/repo_cleanup`.

`exports/` na raiz nao deve existir. Exports prontos para publicacao pertencem a `data/exports`.

PostgreSQL e o banco oficial do portal e do pipeline visual. SQLite pode existir apenas em settings ou scripts explicitamente marcados para teste, migracao ou compatibilidade local.

## Regras

- `gaiden/` contem o core do pipeline.
- `web/` aciona, exibe e registra estados; nao deve ser dono do pipeline.
- Escritas fisicas relevantes devem passar por `gaiden.infrastructure.storage` ou `gaiden.infrastructure.paths`.
- `GAIDEN_DATA_ROOT` e a variavel canonica para resolver o storage.
- `GAIDEN_STORAGE_ROOT` permanece aceito apenas como alias legado.
- `GAIDEN_DATA_ROOT` nao pode apontar para `web/data`.
- Artefatos de livro devem ficar sob `data/`.

## Consequencias

- Caminhos novos devem usar a camada central de paths.
- Residuos como `sqlite3`, `web/data` e `exports/` devem ser movidos para backup datado antes de qualquer remocao definitiva.
- Auditorias de arvore devem reportar desvios antes de releases ou refactors maiores.
