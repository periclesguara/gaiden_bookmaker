# AGENTS.md — Gaiden BookMaker

These instructions apply to the entire repository. More specific
`AGENTS.md` files may add constraints for a subtree, but may not weaken the
security, data-safety, migration, or validation requirements below.

## Mission

Codex must leave Gaiden safer, testable, documented, and operational. Audit
findings and suggestions are hypotheses to verify against the current branch,
current pull requests, current database model state, and current CI. Never
apply an old recommendation merely because it was previously written.

Communicate progress and handoff notes in Portuguese. Code, identifiers, commit
messages, and technical documentation may remain in English when that is the
existing local convention.

## Required context before changing code

Before editing:

1. read `README.md`, `SECURITY.md`, `docs/architecture.md`, and
   `docs/repository-governance.md`;
2. inspect the current default-branch head, open pull requests, changed files,
   and workflow results;
3. identify whether the work affects source, migrations, persistent data,
   runtime artifacts, credentials, or stacked branches;
4. state the exact scope and keep unrelated cleanup out of the change;
5. preserve user changes and operator data.

If a suggestion conflicts with the current tree or a newer verified result,
update the plan and document why. Do not force the repository to match a stale
report.

## Branches and pull requests

- Never develop directly on `main`.
- Start new work from the current approved base, not a convenient stale feature
  branch.
- Use one focused branch and pull request per risk class:
  - security and credentials;
  - migration repair;
  - application behavior;
  - tests and CI;
  - generated-data cleanup;
  - compatibility-code retirement.
- State the exact base branch, dependencies, persistent-data impact, security
  impact, tests executed, and superseded work.
- Do not retarget or merge the old stacked PR chain as a single operation.
- Use squash merge for focused changes after all required checks pass.
- Do not merge a draft, a moving head, or a PR with unresolved validation.

## Security rules

- Never commit, print, copy into issues, or expose credentials, tokens,
  passwords, connection strings, private keys, personal data, or secret values.
- Secrets must come from environment variables or an approved secret manager.
- Examples must contain unmistakably nonfunctional placeholders.
- Keep GitHub Actions permissions at the minimum required level.
- Pin third-party actions to full commit SHAs.
- Do not add `continue-on-error`, broad write permissions, credential
  persistence, or unbounded workflow execution to obtain a green check.
- Run the repository hygiene check and current-tree secret scanner for every PR.
- Treat previously committed credentials as compromised until they are rotated;
  deleting a file is not credential rotation.

## Persistent data and generated artifacts

Git stores source, migrations, tests, small intentional fixtures, configuration
templates, and documentation. Do not track manuscripts, chunks, builds,
translations, covers, images, EPUB/PDF outputs, databases, dumps, logs,
backups, or operator exports.

Before removing tracked runtime material:

1. resolve the exact paths with a read-only comparison;
2. confirm that source, migrations, tests, and documentation are outside the
   deletion set;
3. document the operator backup location and recovery commit;
4. keep the removal in a separate PR;
5. add a hygiene rule that prevents recurrence.

Never delete or overwrite canonical operator data merely to tidy the repository.
History rewriting and force-pushing require a separate approved runbook,
verified backup, exact ref list, and explicit authorization.

## Django and PostgreSQL migrations

- PostgreSQL 16 with pgvector is the integration target.
- Never fabricate a missing historical migration, use `--fake`, use an empty
  placeholder to hide a broken dependency, or rewrite an applied migration
  without verified provenance.
- Prefer forward repair migrations.
- Recover historical migration sources only from a verifiable commit, branch,
  artifact, or approved specification, and document the provenance.
- A state-only migration must be tested against the physical schema. Preserved
  `NOT NULL` columns must remain represented in active models or receive a
  safe, explicit database strategy; otherwise ORM inserts can fail.
- Never generate destructive removal migrations merely because legacy physical
  columns are absent from current models.
- Before merge, run a clean migration, migration-drift check, pending-migration
  check, and representative ORM writes.
- Applying migrations to a persistent environment requires a verified backup,
  disposable-clone rehearsal, migration-plan review, and rollback notes.

## Tests and CI

A successful command that discovers zero tests is a failure of the quality
process.

- Do not reduce or bypass the protected Django test minimum.
- Every bug fix must include a regression test that fails before the fix.
- Every changed behavior must have direct tests at the lowest useful layer.
- Keep tests deterministic, offline by default, and independent of real
  credentials or production data.
- Mock external APIs; never call OpenAI, Google Drive, publishing platforms, or
  other paid/external services from ordinary CI.
- Validate at least:
  - Django system checks;
  - migration drift;
  - clean PostgreSQL migration;
  - pending migrations;
  - Python and shell syntax;
  - the protected Django suite;
  - dependency vulnerabilities;
  - repository hygiene and secrets.
- Do not weaken assertions, delete tests, lower thresholds, or exclude failing
  modules solely to make CI green.

## Active architecture and compatibility code

- `gaiden/` is the reusable editorial-processing core.
- `web/` is the Django operator application.
- Put reusable business rules in service modules; keep views focused on HTTP
  coordination.
- Files suffixed `_2025.py`, book-specific scripts,
  `setup_translate_2025.sh`, and `settings_sqlite.py` are compatibility
  candidates, not automatic deletion targets.
- Before retiring compatibility code, search all callers, identify and test the
  replacement, document rollback, and remove it in a dedicated PR.
- New production code must not create new dependencies on compatibility modules
  unless the PR explicitly justifies that decision.

## Documentation

Update documentation in the same PR when behavior, architecture, deployment,
storage, migration, security, or operator workflow changes.

Documentation must describe what is true after the change, not only what was
attempted. Include exact commands, constraints, recovery information, and known
limitations. Do not claim full validation when only syntax, discovery, or a
partial workflow was checked.

## Definition of done

Codex may recommend merge only when:

- the scope is focused and the current head SHA was reviewed;
- Security and Main CI pass on that head;
- tests exercise the changed behavior and the protected minimum is maintained;
- migration and data effects are documented and non-destructive;
- no secret or generated runtime artifact was introduced;
- user/operator documentation is current;
- the PR body records validation evidence and recovery steps;
- remaining risks are explicit rather than hidden.

If any condition cannot be satisfied, leave the PR in draft and report the
specific blocker. Never describe incomplete or unverified work as finished.
