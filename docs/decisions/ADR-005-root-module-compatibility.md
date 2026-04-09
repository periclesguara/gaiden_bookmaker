# ADR-005: Root Module Compatibility

Status: accepted

Date: 2026-04-08

## Decision

Os módulos raiz de `gaiden/` permanecem como shims compatíveis durante a transição.

## Official mapping

- `gaiden.about` -> `gaiden.domain.editorial.about`
- `gaiden.ingest` -> `gaiden.application.pipeline.ingest`
- `gaiden.normalize` -> `gaiden.application.pipeline.normalization`
- `gaiden.translate` -> `gaiden.application.pipeline.translation`
- `gaiden.openai_client` -> `gaiden.infrastructure.openai_client`
- `gaiden.secrets` -> `gaiden.infrastructure.env`
