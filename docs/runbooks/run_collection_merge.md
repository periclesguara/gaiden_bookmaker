# Run Collection Merge

## Pré-condições

- todos os itens cadastrados
- todos os uploads recebidos
- preparação concluída
- normalize por item concluído

## Execução

1. rodar preparação da Collection
2. confirmar arquivos em `prepared/`
3. rodar normalize da Collection
4. confirmar arquivos em `normalized_items/`
5. rodar merge
6. revisar arquivo em `merged/`
7. confirmar `manifest.json`

## Resultado esperado

- source único e estruturado
- marcadores `BOOK ONE`, `BOOK TWO`, ...
- título de cada obra preservado
- capítulos preservados
- Collection apta para handoff ao pipeline padrão
