# Importação editorial incremental por blocos

O serviço incremental reutiliza as entidades do app Django `pipeline` e aponta
para `editorial.Edition`; não existe um pipeline editorial paralelo nem regra
específica para uma obra. A porta web fica no modo Automated do Intake e a
orquestração genérica fica em `gaiden.application.editorial_import`.

## Implantação

```bash
source .venv/bin/activate
export DJANGO_SETTINGS_MODULE=gaiden_portal.settings
export PYTHONPATH=web
python web/manage.py migrate pipeline
python web/manage.py check
```

## Interface

Acesse `/intake/automated/`. Selecione o pacote editorial JSON, o manifesto e a
pasta com fonte, corpos e blocos. A prévia usa somente staging temporário e não
grava no banco, no corpo canônico ou no Drive. A confirmação usa token assinado,
relê os arquivos e recusa conteúdo alterado depois da prévia.

O destino pode ser:

- uma pasta local ou sincronizada;
- um remoto `rclone`, por exemplo
  `gaiden_drive:04_TRANSLATION_JOBS/book_XXXX/pt-br/return`.

O remoto `gaiden_drive:` já aponta para a raiz da pasta Gaiden Bookmaker; não
repita `Gaiden Bookmaker/` no caminho.

## Linha de comando

```bash
python web/manage.py incremental_blocks preview \
  --manifest /caminho/control/manifest.json \
  --blocks-dir /caminho/blocks

python web/manage.py incremental_blocks import \
  --manifest /caminho/control/manifest.json \
  --blocks-dir /caminho/blocks \
  --attempt 1

python web/manage.py incremental_blocks resume \
  --edition-id 'book_0041:pt-BR:1'

python web/manage.py incremental_blocks export \
  --edition-id 'book_0041:pt-BR:1' \
  --destination 'gaiden_drive:04_TRANSLATION_JOBS/book_0041/pt-br'
```

Uma nova tentativa deliberada do mesmo manifesto deve incrementar `--attempt`.
Reimportações preservam idempotência por conteúdo e não duplicam versões. Na
interface Automated, catálogo, frontmatter, blocos e corpo do lote confirmado
participam da mesma transação; uma falha reverte o lote atual inteiro. Lotes já
confirmados em execuções anteriores permanecem disponíveis para retomada.

Para gerar um manifesto novo a partir de um índice explícito de identidades e
arquivos, use `scripts/build_incremental_block_manifest.py`. Esse gerador
substitui o protótipo específico de `book_0041` e não deriva identidade de nomes
temporários de arquivo.

## Publicação

O exportador envia somente blocos novos, alterados ou com estado editorial
alterado. Cada arquivo é publicado primeiro com nome temporário, conferido por
tamanho e SHA-256 e então movido ao nome definitivo. A ordem dos controles é:

1. `resume-state.json`;
2. `errors.json`;
3. `manifest.json`;
4. `import-ack.json` (sempre por último).

Os campos de exportação no banco só são confirmados depois que todas essas
publicações terminam sem erro.

## Testes

```bash
PGHOST=unused PGDATABASE=unused PGUSER=unused \
DJANGO_SETTINGS_MODULE=gaiden_portal.settings_test PYTHONPATH=web \
.venv/bin/python web/manage.py test \
  pipeline.test_editorial_import pipeline.test_incremental_import
```

A configuração de testes usa SQLite e ignora apenas a migration PostgreSQL da
extensão pgvector. O runtime oficial continua exigindo PostgreSQL.
