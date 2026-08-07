# Main migration graph reconciliation

Date: 2026-08-07  
Scope: PR #11, branch `agent/main-ci-and-dependencies`

## Outcome

The default-branch migration graph referenced
`editorial.0021_alter_edition_language_code_and_more` from
`pipeline.0017_pipelinerunstate`, but the referenced editorial migration and
its predecessors were absent from the checked-in source tree. Django therefore
raised `NodeNotFoundError` before it could perform a migration drift check.

Ten original migration sources were recovered verbatim from the repository's
`integrate/runner` branch:

- both `editorial.0013` branches;
- `editorial.0014` and their merge node `0015`;
- the contiguous `editorial.0016` through `0021` chain.

No empty migration, `--fake`, dependency rewrite, or
`continue-on-error` was used.


## Clean-bootstrap repair

Full migration application exposed a second historical inconsistency:
`editorial.0005_edition_about_edition_text` attempted to add
`about_edition_text`, although the checked-in `0001_initial` already created
that field. The canonical `0005` source on `integrate/runner` has an empty
operation list and a distinct blob identity. The branch version was restored
verbatim from that trusted repository source.

After restoration, a clean PostgreSQL 16 database successfully applies the
complete graph, `migrate --check` reports no pending migration, and the
dependency audit reports no known vulnerability in the declared requirements.

## Active-model reconciliation

The recovered historical state contains columns and the `EditionBlock` table
that are not represented by the active main models. Automatically generated
removal migrations would have dropped those database objects and risked
destroying canonical historical data.

Two explicit reconciliation migrations were added:

- `editorial.0022_reconcile_legacy_state`;
- `pipeline.0020_reconcile_legacy_state`.

Both use `SeparateDatabaseAndState` with empty `database_operations`.
They update only Django's project state. Historical columns, tables, and values
remain physically intact and available for a future, separately reviewed
recovery or archival decision.

## Clean-database validation

GitHub Actions provisions PostgreSQL 16 with pgvector and performs:

1. Django system checks;
2. migration drift detection;
3. migration plan generation;
4. full migration application on the ephemeral database;
5. verification that no migrations remain unapplied;
6. Python compilation and the discovered Django test suite;
7. dependency vulnerability auditing.

The CI database is disposable. This reconciliation has not been applied to the
canonical production database by this pull request.

## Deployment

Before applying the migrations to a persistent environment:

1. take and verify a PostgreSQL backup;
2. run the same commands against a disposable clone;
3. inspect `migrate --plan`;
4. apply normally without `--fake`;
5. verify application startup and representative editorial reads.

Because the reconciliation operations are state-only, applying them does not
drop, alter, or backfill canonical tables.

## Rollback

If deployment must be reversed, retain the recovered historical migration
files in source control. Roll back application code first and use a forward
repair migration if the target model state changes again. Do not delete the
recovered migration sources or rewrite already-shared migration history.

The state-only reconciliation can be reversed in an isolated clone for
diagnosis; its database portion is empty, so physical legacy data is unchanged
in either direction.
