# ADR-002: Banco vs Filesystem

Status: accepted

Date: 2026-04-08

## Decision

- Filesystem guarda artefatos do pipeline.
- Banco guarda metadados, índices, templates editoriais e estado operacional.
- Existência de arquivo é evidência, não prova suficiente de sucesso.

## Rules

- Status operacional deve usar estados explícitos: `pending`, `running`, `failed`, `completed`, `skipped`, `blocked`.
- Artefato crítico deve ser validado no mínimo por existência e tamanho mínimo.
- Gates críticos devem falhar cedo quando pré-condições não forem satisfeitas.

## Compatibility

- Paths e nomes legados continuam aceitos por wrappers compatíveis enquanto o fluxo migra.
- Views Django passam a consumir gates/status centrais sem alterar os contratos externos da UI.
