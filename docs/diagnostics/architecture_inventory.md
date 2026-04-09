# Architecture Inventory

Date: 2026-04-08

## Official layers

- `gaiden/domain/`: conceitos editoriais e contratos puros
- `gaiden/application/`: casos de uso e orquestração
- `gaiden/infrastructure/`: storage, env/secrets, OpenAI
- `gaiden/interfaces/`: CLI e entrypoints formais
- `web/`: interface operacional Django

## Governance matrix

- matriz operacional oficial: `docs/decisions/system-governance-matrix.md`
- classificação permitida por componente:
  - `[oficial]`
  - `[compat]`
  - `[legado]`
  - `[passivo]`

## Root modules under controlled migration

- `gaiden/about.py` -> wrapper para `gaiden.domain.editorial.about`
- `gaiden/ingest.py` -> wrapper para `gaiden.application.pipeline.ingest`
- `gaiden/normalize.py` -> wrapper para `gaiden.application.pipeline.normalization`
- `gaiden/translate.py` -> wrapper para `gaiden.application.pipeline.translation`
- `gaiden/openai_client.py` -> wrapper para `gaiden.infrastructure.openai_client`
- `gaiden/secrets.py` -> wrapper para `gaiden.infrastructure.env`

## Web hotspots

- `web/pipeline/views.py` ainda é o principal concentrador de orquestração
- `web/pipeline/services/` já contém parte da camada adaptadora
- `web/pipeline/services/preflight.py` e `canonical_merge.py` já apontam para caminhos oficiais novos

## Pending hotspots

- `web/pipeline/views.py` ainda precisa de extrações adicionais
- `web/pipeline/tests.py` ainda referencia vários paths legados diretamente
- scripts one-off ainda coexistem com a nova CLI
