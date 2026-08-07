# Gaiden BookMaker

Gaiden BookMaker is RinoBooks' private editorial production platform. It
coordinates intake, text normalization, translation and refinement workflows,
editorial metadata, image assets, and validated digital book builds.

## Repository status

- The default branch is the protected integration baseline.
- Feature work is reviewed through pull requests.
- Generated books, local databases, credentials, backups, and operator data do
  not belong in Git.
- Canonical editorial artifacts live in the configured external storage, not in
  repository history.

The active architecture is evolving through stacked pull requests. See
`docs/repository-governance.md` before retargeting or merging a branch.

## Local environment

Requirements:

- Python 3.10 or newer;
- PostgreSQL 16 with pgvector for the integrated application;
- a local environment containing the variables listed in `.env.example`;
- EPUBCheck and Pandoc for the build paths that use them.

Never place usable credentials in `.env.example`, shell scripts, source files,
issues, pull requests, or Actions logs.

At minimum, export unique values for:

```text
DJANGO_SECRET_KEY
PGPASSWORD
OPENAI_API_KEY
```

Install the declared environment and then load the non-secret runtime defaults:

```bash
python -m pip install -r requirements-ci.txt
python -m pip install --no-deps -e .
source scripts/ops/env_gaiden.sh
```

## Required checks

Before merging:

```bash
bash scripts/ci/check_repo_hygiene.sh
python3 scripts/ci/scan_tracked_secrets.py
python3 -m py_compile scripts/ci/scan_tracked_secrets.py
```

Feature branches must additionally run their relevant Django, PostgreSQL,
migration, and EPUB validation suites.

## Security

Read `SECURITY.md` before reporting a vulnerability. The credential incident
and approved recovery sequence are documented in
`docs/runbooks/secret-rotation-and-history-cleanup.md`.

## Documentation

- `docs/repository-governance.md`: branch, pull request, and artifact policy.
- `docs/runbooks/secret-rotation-and-history-cleanup.md`: credential response.
- Architecture-specific documents live under `docs/` and should be updated in
  the same pull request as the behavior they describe.

## Ownership

Private software and editorial infrastructure of RinoBooks.
