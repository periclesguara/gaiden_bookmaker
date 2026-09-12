# Writer OpenAI Luna validation

Scope: Writer generation, supporting-cast generation, CLI, UI labels, and Writer
operator documentation. Base: `main` at
`b04e3fad01717f3fd985c17e0c5bd84f424ae53d`. No dependency PRs or superseded PRs.
No migrations or persistent-data changes. Other model routes and RAG embeddings
are unchanged. Existing credentials are read, never copied or rewritten.

The clean branch uses the existing `gaiden.secrets_loader` credential reader.
The local workspace has a newer, uncommitted Writer implementation and uses its
existing `gaiden.infrastructure.env` reader instead. Its adapter also removes
the generation-only Ollama gate and updates the dashboard. Existing local
validation, session repair, and operator data are preserved.

Validation on 2026-09-12:

- PostgreSQL 16 / pgvector disposable container: Django checks, migration drift,
  clean migrations and pending migrations passed.
- `python web/manage.py test editorial pipeline writer --noinput --verbosity 0`:
  79 tests passed against PostgreSQL; the protected minimum is unchanged.
- Local Writer: `python web/manage.py test web.writer.test_openai_generator
  web.writer.tests web.writer.test_validation --settings=gaiden_portal.settings_test
  --noinput`: 77 tests passed. This local run uses the existing isolated SQLite
  test settings and does not validate the local persistent migration history.
- Writer adapter tests first failed before implementation, then passed with mocked
  APIs. No model generation, corpus upload or paid API request was made.
- Python compilation, shell syntax, clean-branch repository hygiene and tracked
  secret scan passed.
- `python -m pip_audit -r requirements-ci.txt --progress-spinner=off` failed:
  Django 5.2.16, PYSEC-2026-3717; reported fixes 5.2.17 or 6.0.8. Dependency repair
  is outside this Writer-only change. Installed local Django is 5.2.11; the clean
  branch tests ran with that existing environment, not a fresh requirements install.
- Existing local-tree hygiene and secret scans fail on pre-existing runtime
  artifacts/assignments. They have not been removed or exposed in this change.
- Local Django restarted with its existing command/environment after a read-only
  check found zero active Writer generations. `/writer/` returned HTTP 200 and
  displayed GPT-5.6 Luna. API key presence was verified without displaying it;
  actual account access to Luna has not been tested through a paid request.

Recovery: revert this focused commit and restart Writer only after confirming no
active generation. No database rollback or index rebuild is required. Keep all
existing sessions and runtime indexes. Local pre-change Writer files were saved
at `/tmp/gaiden-writer-before-luna` for targeted recovery; do not overwrite newer
operator edits with those files. That temporary directory is not a durable backup.

Do not merge until required Security and Main CI checks pass on the reviewed head.
