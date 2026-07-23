# ADR-008: Batch Intake Boundary

Status: accepted

Date: 2026-07-16

## Decision

`Collection` e `IntakeBatch` resolvem problemas editoriais diferentes e não
compartilham o mesmo ciclo operacional.

- `Collection` reúne vários itens para formar um único livro.
- `IntakeBatch` recebe vários arquivos que formarão livros independentes.
- Não existe merge entre `IntakeItem`s.
- Cada item possui seu próprio ciclo de limpeza e tradução.
- O pipeline existente não será modificado nesta primeira versão.
- O handoff do Intake termina em `READY_FOR_EDITING`.
- Capa, imagens, frontmatter e build pertencem ao segundo bloco do produto.

## State and storage boundaries

- PostgreSQL guarda metadados e estados do lote e dos itens.
- O filesystem canônico guarda originais, texto limpo, manifestos e retornos.
- O namespace do módulo é `data/intake/<batch>/<source_language>/`.
- Intake não escreve em `data/raw`, `data/collections` ou em storage web.
- Um item não cria `Work`, `Edition` ou `BookEditionTemplate` nesta versão.

## Consequences

- Cada arquivo pode avançar ou falhar sem bloquear os demais livros do lote.
- A tradução é preparada para execução externa pelo Codex; o Gaiden não chama
  APIs de modelos.
- O retorno traduzido precisa corresponder ao item e ao idioma registrados no
  manifesto antes da confirmação editorial.
