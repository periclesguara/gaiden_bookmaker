# Gaiden BookMaker

Gaiden BookMaker is RinoBooks' private editorial production platform. It
coordinates intake, text normalization, translation and refinement workflows,
editorial metadata, image assets, and validated digital book builds.

## Repository status

- The default branch is the stable integration baseline.
- Feature work is reviewed through pull requests.
- Generated books, local databases, credentials, backups, and operator data do
  not belong in Git.
- Canonical editorial artifacts live in the configured external storage, not in
  repository history.

The application has a reusable editorial core in `gaiden/` and a Django
operator interface in `web/`. See `docs/architecture.md` for ownership and
runtime-data boundaries. See `docs/repository-governance.md` before
retargeting or merging a branch.

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
python web/manage.py check
python web/manage.py makemigrations --check --dry-run
python web/manage.py test editorial pipeline
```

The GitHub Actions application workflow additionally provisions PostgreSQL 16
with pgvector, applies the complete migration graph, enforces a minimum Django
test baseline, and audits pinned dependencies.

## Security

Read `SECURITY.md` before reporting a vulnerability. The credential incident
and approved recovery sequence are documented in
`docs/runbooks/secret-rotation-and-history-cleanup.md`.

## Documentation

- `docs/architecture.md`: active modules, compatibility boundary, runtime data,
  and quality gates.
- `docs/repository-governance.md`: branch, pull request, and artifact policy.
- `docs/runbooks/secret-rotation-and-history-cleanup.md`: credential response.
- `docs/audits/main-migration-reconciliation.md`: recovered migration
  provenance, data-preserving reconciliation, deployment, and rollback.
- `docs/writer-qwen-rag.md`: phase-1 Writer rules, Qwen model operation,
  complete Sherlock indexing, RAG, draft generation, and the phase-2 gate.
- `docs/writer-workflow.md`: private-network deployment, file treatment, bibles,
  chapter parameters, four-session generation, review, and finalization.
- `docs/runbooks/chapter-translation-drive-v2.md`: chapter-based translation
  jobs, Drive layout, validation, deterministic merge, migration, and rollback.
- `docs/runbooks/block-01-normalize-qwen-v2.md`: authoritative Block 01 RAW,
  Qwen JSON normalization, structure split, return/merge, and Block 02 gate.

Architecture-specific documents must be updated in the same pull request as the
behavior they describe.

## Ownership

Private software and editorial infrastructure of RinoBooks.
