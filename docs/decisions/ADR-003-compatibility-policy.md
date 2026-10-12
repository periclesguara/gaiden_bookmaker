# ADR-003: Compatibility and Deprecation Policy

Status: accepted

Date: 2026-04-08

## Decision

Mudanças estruturais serão feitas em fases com compatibilidade primeiro.

## Policy

- Introduzir novo serviço/helper antes de remover caminho antigo.
- Reapontar chamadas antigas para a camada nova.
- Marcar legado e aliases como compatibilidade transitória.
- Só remover quando não houver mais referências ativas.

## Current compatibility layers

- `gaiden.secrets` agora delega para `gaiden.infrastructure.env`.
- `web/pipeline/services/paths.py` delega para `gaiden.infrastructure.storage`.
- Views e serviços web podem continuar chamando helpers antigos enquanto a lógica interna converge.
- a classificação operacional final por componente está consolidada em `docs/decisions/system-governance-matrix.md`.
