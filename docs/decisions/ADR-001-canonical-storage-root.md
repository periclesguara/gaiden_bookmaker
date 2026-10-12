# ADR-001: Canonical Storage Root

Status: accepted

Date: 2026-04-08

## Decision

O root canônico de storage operacional do Gaiden BookMaker é `repo/data`.

`repo/web/data` fica explicitamente deprecado e não deve mais ser usado como storage paralelo de artefatos do pipeline.

## Rationale

- `repo/data` já contém os artefatos reais do pipeline.
- `repo/web/data` introduz duplicidade estrutural e risco operacional.
- Django/web deve consumir o mesmo storage do core, via configuração central.

## Consequences

- Toda resolução de path de artefatos deve passar por `gaiden.infrastructure.storage`.
- `web/data` pode permanecer temporariamente apenas como passivo legado controlado.
- Se `web/data` voltar a receber runtime artifacts, isso deve ser tratado como desvio operacional e migrado.
