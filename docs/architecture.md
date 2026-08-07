# Gaiden architecture

## Active application

Gaiden has two deliberate layers:

- `gaiden/` contains reusable editorial processing code and command-line tools;
- `web/` contains the Django operator application.

The Django project is `web/gaiden_portal` and installs two first-party apps:

- `editorial`: canonical works, editions, contributors, metadata, frontmatter,
  and editorial artifacts;
- `pipeline`: jobs, runs, source selection, build paths, and operator flows.

Reusable business logic belongs in service modules. Views should coordinate
HTTP input and output rather than implement filesystem or editorial rules.

## Runtime data boundary

Git is the source-of-truth for code, migrations, tests, small fixtures,
configuration templates, and documentation. It is not the canonical store for:

- manuscripts or chunks;
- generated EPUB, PDF, Markdown, images, or covers;
- databases, backups, logs, credentials, or operator exports.

Runtime artifacts belong under the configured external storage. Paths under
`data/` are compatibility locations and must remain ignored unless a small,
reviewed fixture is intentionally added under a dedicated fixture directory.

## Compatibility inventory

The following names indicate compatibility code, not automatic deletion
candidates:

- modules suffixed `_2025.py`;
- `setup_translate_2025.sh`;
- book-specific build and normalization scripts;
- `web/gaiden_portal/settings_sqlite.py`.

Before removal, each item must have its callers searched, its replacement
identified, and a rollback commit recorded. New production paths must not add
dependencies on these compatibility modules.

## Quality gates

Every pull request must pass:

1. repository hygiene and current-tree secret scanning;
2. Python and shell syntax checks;
3. PostgreSQL 16 plus pgvector clean migration;
4. migration drift and pending-migration checks;
5. at least the protected Django test baseline;
6. dependency vulnerability auditing.

The minimum test count is a regression guard, not a coverage target. New or
changed behavior must add direct tests.

## Change boundaries

Migration repair, runtime-data cleanup, compatibility-code retirement, and
feature integration are separate review units. Persistent data is never
deleted merely to make model state match source code.
