# Directory Contract

## Canônico

- `data/`: root operacional único do pipeline
- `data/raw/`: entradas fonte
- `data/normalized/`: texto normalizado
- `data/chunks/`: artefatos intermediários de split/chunk/refine
- `data/translated/`: saídas de tradução e merges transitórios
- `data/frontmatter/`: frontmatter editorial
- `data/builds/`: outputs por edição/idioma
- `data/covers/`: capas
- `data/images/`: imagens editoriais
- `data/editions/`: artefatos por edição operacional
- `data/tmp/`: transitório, não auditável
- `data/db/`: banco local do storage operacional

## Deprecado

- `web/data/`: não é root canônico; manter apenas enquanto houver passivo legado a migrar

## Regra operacional

- Paths do pipeline devem ser resolvidos por `gaiden.infrastructure.storage`
- Não concatenar manualmente `data/...` em código novo
- Artefato final e temporário não devem compartilhar a mesma semântica de sucesso
