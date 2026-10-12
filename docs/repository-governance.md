# Repository governance

## Purpose

Git stores source code, migrations, small fixtures, tests, configuration
templates, and documentation. It is not the storage layer for manuscripts,
generated books, databases, credentials, backups, images, or operator exports.

## Branch model

- `main`: stable integration baseline.
- `security/*`: isolated security remediation.
- `agent/*`, `feature/*`, `fix/*`, `refactor/*`: review branches.
- `integration/*`: temporary consolidation branches for stacked work.

New feature branches should start from the current approved integration base.
Do not create new work from a stale feature branch merely because it contains a
needed module; integrate that dependency first.

## Pull requests

Every pull request must state:

- what changed and why;
- its exact base and dependency PRs;
- migrations and persistent-data impact;
- security and credential impact;
- tests run against the current head SHA;
- whether another PR is superseded.

Draft PRs are work in progress, not permanent storage. Close a superseded PR
only after its replacement is identified and unique commits are accounted for.

## Merge policy

The default branch should require:

- pull requests;
- successful security and application checks;
- an up-to-date head branch;
- resolved review conversations;
- blocked force-pushes and branch deletion.

Use squash merge for focused feature work and merge commits only when preserving
a deliberate integration history. Delete merged remote branches.

## Stacked pull requests

For the current Gaiden stack:

1. merge isolated security remediation first;
2. build an explicit dependency graph;
3. create a clean `integration/gaiden-YYYY-MM` branch;
4. replay or merge one dependency layer at a time;
5. run the full PostgreSQL, migration, and editorial build suite;
6. close superseded drafts with a link to the surviving implementation.

Never retarget the whole stack to `main` in one operation.

## Generated and sensitive material

The hygiene workflow rejects known credential files, local databases, backups,
Python caches, and generated ebook formats. New artifact classes must be added
to both `.gitignore` and `scripts/ci/check_repo_hygiene.sh`.

## History rewriting

Rewriting shared history and force-pushing are destructive operations. They
require a frozen push window, a restricted mirror backup, an exact ref list, a
post-rewrite scan, and explicit approval of the force-push set.
