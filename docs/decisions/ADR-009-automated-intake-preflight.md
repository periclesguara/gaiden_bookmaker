# ADR-009: Automated mode inside Intake

Status: proposed

Date: 2026-07-29

## Decision

The automated editorial flow is a mode inside the existing `intake_module`.
It is not a new application and does not bypass the canonical Intake identity,
book-code reservation, source provenance, or pipeline handoff.

The first increment is a read-only preflight and deterministic JSON plan. It:

- requires an already reserved canonical `book_code`;
- blocks `book_0034` through `book_0040`, reserved for Tarzan;
- validates title, author, year, source path, source SHA-256 and source state;
- plans six editions: `en-gb`, `fr-fr`, `pt-br`, `it-it`, `de-de`, and `es-es`;
- lists localization/translation, body, frontmatter, introduction, image bank,
  cover, end marker, build and EPUB validation stages;
- produces no database, filesystem, Drive, translation, edition or build writes.

## Why this boundary

The migration history remains under reconciliation. A migration-free,
read-only slice can be reviewed and tested without activating recovered
migrations or writing to the canonical database.

## Follow-up boundary

Execution will be added only after the plan is approved and the migration
baseline is reconciled. The mutating phase must be POST-only, transactional,
idempotent, resumable, auditable and explicit about human approvals. A
`public_domain` checkbox is only a precondition; it is not a legal conclusion
for every distribution territory.
