# ADR-004: web/data Policy

Status: accepted

Date: 2026-04-08

## Decision

`web/data` não é storage operacional oficial. O único root canônico é `data/`.

## Allowed use

- documentação transitória
- passivo legado explicitamente auditado

## Forbidden use

- storage paralelo de artefatos do pipeline
- espelhamento silencioso de `data/`
