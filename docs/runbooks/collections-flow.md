# Collections Flow

## Entrada

1. página inicial do sistema
2. escolher `Collection`
3. cadastrar metadata da collection
4. cadastrar itens ordenados
5. enviar HTML de cada item

## Processamento

1. preparação individual
2. normalize individual
3. merge estruturado
4. geração de `manifest.json`
5. revisão do source unificado
6. handoff para a esteira padrão

## Storage canônico

- `data/collections/<collection_code>/<language>/uploads`
- `data/collections/<collection_code>/<language>/prepared`
- `data/collections/<collection_code>/<language>/normalized_items`
- `data/collections/<collection_code>/<language>/merged`
- `data/collections/<collection_code>/<language>/manifest.json`

## Status oficiais

- `COLLECTION_CREATED`
- `COLLECTION_ITEMS_REGISTERED`
- `COLLECTION_UPLOADS_RECEIVED`
- `COLLECTION_PREPARED`
- `COLLECTION_NORMALIZED`
- `COLLECTION_MERGED`
- `COLLECTION_READY_FOR_PIPELINE`
- `COLLECTION_PIPELINE_RUNNING`
- `COLLECTION_DONE`
- `COLLECTION_FAILED`

## Handoff

Depois de `COLLECTION_MERGED`, a collection pode ser marcada como `COLLECTION_READY_FOR_PIPELINE` e então entregue ao pipeline padrão.

## Restrições

- `Collection` não usa `web/data`
- uploads de `Collection` não entram em `data/raw/<book_code>`
- merge e normalize batch não rodam dentro da view Django
