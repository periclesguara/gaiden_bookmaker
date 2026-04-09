# Collection Module Inventory

## Núcleo oficial

- `gaiden/application/collections/service.py`
- `gaiden/infrastructure/collections_runner.py`
- `gaiden/infrastructure/collections_storage.py`
- `gaiden/interfaces/collections_cli.py`
- `web/collections_module/`

## Storage oficial

- `data/collections/`

## Blindagens vigentes

- sem uso de `web/data` pela Collection
- sem uso de `data/raw/book_XXXX` na entrada
- merge fora da view
- handoff bloqueado antes de `COLLECTION_MERGED`
- wrappers legados da raiz de `gaiden/` não são dependência principal do módulo `Collection`

## Pontos de atenção

- `web/data/` ainda existe como passivo legado do sistema geral
- frontmatter/md/build da Collection continuam reservados para etapas posteriores
