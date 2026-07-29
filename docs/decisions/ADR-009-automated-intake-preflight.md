# ADR-009: Automated mode inside Intake

Status: proposed

Date: 2026-07-29

## Decision

The automated editorial flow is a mode inside the existing `intake_module`.
It is not a new application and does not bypass the canonical Intake identity,
book-code reservation, source provenance, or pipeline handoff.

The first increment is a read-only bilingual pilot and deterministic JSON plan.
It:

- requires an already reserved canonical `book_code`;
- accepts Tarzan codes `book_0034` through `book_0040` when they are already
  assigned by the canonical reservation flow;
- validates title, author, year, source path, source SHA-256 and source state;
- plans two editions: `en-gb` and `pt-br`;
- distinguishes external locale, canonical language and legacy pipeline token;
- plans source cleaning when the item is only `DOWNLOADED`;
- lists localization/translation, body, frontmatter, introduction, image bank,
  cover, end marker, build and EPUB validation stages;
- produces no database, filesystem, Drive, translation, edition or build writes.

## Language contract

| Edition locale | Canonical language | Pipeline token |
| --- | --- | --- |
| `en-gb` | `en` | `en` |
| `pt-br` | `pt-br` | `ptbr` |

More languages will be added only after the bilingual pilot is validated.

## Why this boundary

The migration history remains under reconciliation. A migration-free,
read-only slice can be reviewed and tested without activating recovered
migrations or writing to the canonical database.

## Follow-up boundary

Execution will be added only after the plan is approved and the migration
baseline is reconciled. Persisting two or more edition runs requires an
explicit run model or a verified reuse of `TranslationJob`; the singular
`IntakeItem.target_language` fields are not sufficient.

The mutating phase must be POST-only, transactional, idempotent, resumable,
auditable and explicit about human approvals. A `public_domain` checkbox is
only a precondition; it is not a legal conclusion for every distribution
territory.
