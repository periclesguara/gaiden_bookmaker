# ADR-007: Collection Module Boundary

Status: accepted

Date: 2026-04-08

## Decision

`Collection` é um fluxo editorial separado de `Book`.

## Boundary

- cadastro próprio
- itens próprios
- uploads próprios
- storage próprio em `data/collections/...`
- preparação e merge próprios
- handoff ao pipeline padrão somente após merged final

## Forbidden

- usar `data/raw/book_*` como input da Collection
- tratar Collection como Book desde a entrada
- executar merge dentro de view Django
