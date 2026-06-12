# Worktree review - 2026-06-12

Branch: `fix/book-0026-ptbr-refine-patch`

Latest commits on branch:

- `3c15844b` Document JSON contract layers for internal agents
- `335fe7ec` Route EN-US translate to internal modernization agent
- `172b1b15` Add internal JSON-first agent architecture for EN-US modernization

## Current state

- No staged changes were found before this review.
- The worktree remains intentionally dirty with many tracked modifications, tracked deletions, and untracked files.
- No destructive cleanup was performed.
- Ignored Python cache directories were removed outside `.venv` and `.git`.
- Backup `.pyc` files under `backups/repo_cleanup/` were left in place because they are inside a local backup tree.
- A dry-run `git clean -fdn` showed too many potentially useful untracked files, so it was not executed.

## Secret and ignore check

- No OpenAI `sk-...` key was found in the searched project files.
- Token/password matches were limited to placeholders, environment variable names, CSRF token references, or configuration defaults.
- `.env` and `.env.*` are ignored, with `!.env.example` kept trackable.
- Runtime database patterns are ignored: `*.sqlite3`, `*.sqlite`, `*.db`, `db.sqlite3`, `sqlite3`, `data/db/`, `data/tmp/`, and `web/db.sqlite3`.
- `docs/audit/` remains broadly ignored because local audit reports already exist there.

## Group 1 - book_0026

Keep together for the current book_0026 Portuguese refinement/release work:

- `scripts/audit/audit_book_0026_ptbr_refine.py`
- `scripts/audit/structure_book_0026_ptbr_subjects.py`
- `scripts/pipeline/apply_book_0026_ptbr_refine_patches.py`
- `scripts/pipeline/build_book_0026_ptbr_body_glossary.py`
- `scripts/pipeline/costura_final_book_0026_ptbr.py`
- `scripts/qa/fix_book_0026_nav_parts_and_glossary_refs.py`
- `scripts/qa/legibility_book_0026_ptbr.py`
- `scripts/qa/microclean_book_0026_ptbr.py`
- `scripts/qa/qa_book_0026_ptbr_epub_structure.py`
- `scripts/qa/qa_final_book_0026_ptbr.py`
- `scripts/qa/validate_book_0026_nav_parts_and_glossary_refs.py`
- `scripts/qa/validate_book_0026_ptbr_patched.py`
- `docs/audit/runs/book_0026_ptbr_*` local reports and diffs, currently ignored.

## Group 2 - next sprint

Useful follow-up work, but not part of the already pushed internal EN-US agent routing commits:

- `gaiden/application/lexical/`
- `gaiden/application/ingest/`
- `gaiden/infrastructure/converters/`
- `gaiden/infrastructure/paths.py`
- `gaiden/interfaces/cli/`
- `assets/brand/`
- `.env.example`
- `docs/runbooks/project_structure.md`
- `docs/decisions/ADR-storage-canonico-data-root.md`
- Tests for lexical rules and MarkItDown integration, including `tests/test_lexical_rules_layer.py`, `tests/test_markitdown_adapter.py`, and `tests/test_markitdown_preprod_service.py`.

## Group 3 - disposable or generated

Do not commit without a specific reason:

- Python caches already removed outside `.venv`, `.git`, and backup folders.
- `gaiden_bookmaker.egg-info/*` generated package metadata.
- Local database/runtime deletions such as `sqlite3` and `web/data/db/gaiden.sqlite3`; these are tracked in history, so any final cleanup should be a deliberate commit.
- `web/data/**` generated build/frontmatter/translation artifacts; currently tracked deletions need an explicit decision before restore or removal commit.
- `data/exports/README.md` and `backups/.gitkeep` placeholder changes should be reviewed before inclusion.

## Group 4 - own branch

Large or cross-cutting changes that should not be folded into the current book_0026/agent-routing branch:

- Web pipeline and UI changes: `web/pipeline/views.py`, `web/pipeline/forms.py`, `web/pipeline/models.py`, `web/pipeline/urls.py`, `web/pipeline/templates/pipeline/*`, and `web/pipeline/services/*`.
- Editorial runtime changes: `web/editorial/*`, `web/gaiden_portal/settings.py`, editorial migrations, and `web/pipeline/management/commands/run_editorial_sanitizer.py`.
- Infrastructure/env/storage changes: `docker-compose.core.yml`, `env.sh`, `run_gaiden.sh`, `pyproject.toml`, `gaiden/infrastructure/storage.py`, and `gaiden/infrastructure/openai_client.py`.
- Chunking/splitting/refinement changes: `gaiden/chunker.py`, `gaiden/chapter_agent_split.py`, `web/pipeline/services/chapter_agent.py`, and `web/pipeline/test_split_by_chapter_agent.py`.
- Script reorganization from tracked `scripts/*.py` files into `scripts/pipeline/`, `scripts/migration/`, `scripts/dev/`, `scripts/ops/`, and `scripts/release/`.
- Editorial/Seneca/glossary/structure modules and their tests.
- Russian revolutionary tradition corpus cleanup and bias-neutralization scripts.

## Verification

- Agent routing and contract tests: 16 passed.
- `python -m compileall gaiden`: passed.
- JSON contract validation with `python -m json.tool`: passed.

## Recommendation

1. Preserve this branch for the pushed internal EN-US agent architecture/routing commits and the current book_0026 review notes.
2. Move Group 4 into a dedicated branch before continuing broad pipeline/UI/storage work.
3. Decide explicitly whether tracked generated artifacts under `web/data/**` and local database files should be restored or removed in a separate cleanup commit.
4. Keep `docs/audit/` ignored unless the project intentionally starts versioning selected audit snapshots.
