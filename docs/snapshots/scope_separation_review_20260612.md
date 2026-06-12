# Scope separation review - 2026-06-12

Branch: `fix/book-0026-ptbr-refine-patch`

## Goal

Separate the remaining large worktree changes before the real EN-US smoke test for `modernize_en_us_2026`. No new feature was implemented and the smoke test was not run.

## Current status

- No staged changes existed at the start of this review.
- Runtime data artifacts were already removed from Git in `5e885f6a`.
- `gaiden_bookmaker.egg-info/` was tracked and identified as local Python packaging output.
- Local reference patches were generated under `/tmp/gaiden_worktree_patches/`.

## Group A - book_0026 / immediate patch

Likely book_0026-specific work:

- `scripts/audit/`
- `scripts/pipeline/`
- `scripts/qa/`
- ignored local audit reports under `docs/audit/runs/book_0026_ptbr_*`

These should be committed only if the next step is to finish the book_0026 PT-BR refinement flow.

## Group B - pipeline/web/UI

Needs branch: `feature/pipeline-ui-runner-cleanup`

- `web/pipeline/forms.py`
- `web/pipeline/models.py`
- `web/pipeline/urls.py`
- `web/pipeline/views.py`
- `web/pipeline/templates/pipeline/book_edition_form.html`
- `web/pipeline/templates/pipeline/edition_steps.html`
- `web/pipeline/services/*`
- `web/pipeline/tests.py`
- `web/pipeline/test_split_by_chapter_agent.py`
- `web/pipeline/migrations/*`
- `web/pipeline/management/commands/run_editorial_sanitizer.py`

Reference patch:

- `/tmp/gaiden_worktree_patches/web_pipeline.patch`

## Group C - storage/env/docker

Needs branch: `feature/storage-env-cleanup`

- `docker-compose.core.yml`
- `env.sh`
- `run_gaiden.sh`
- `.env.example`
- `pyproject.toml`
- `gaiden/infrastructure/storage.py`
- `gaiden/infrastructure/openai_client.py`
- `web/gaiden_portal/settings.py`
- `gaiden/infrastructure/paths.py`

Reference patch:

- `/tmp/gaiden_worktree_patches/storage_env.patch`

## Group D - chunk/split/refine/legacy agent flow

Needs branch: `feature/legacy-pipeline-refine-cleanup`

- `gaiden/chunker.py`
- `gaiden/chapter_agent_split.py`
- `gaiden/application/pipeline/normalization.py`
- `gaiden/application/pipeline/translation.py`
- `gaiden/tools/agent_translate_default.py`
- `gaiden/tools/aldebaran_refine_return.py`
- `gaiden/translate_en_modern.py`
- deleted legacy root scripts that appear moved into `scripts/*`

Reference patch:

- `/tmp/gaiden_worktree_patches/legacy_pipeline_refine.patch`

## Group E - editorial/glossary/structure/Seneca

Needs branch: `feature/editorial-structure-glossary`

- `gaiden/application/editorial/`
- `gaiden/application/glossary/`
- `gaiden/application/structure/`
- `web/editorial/*`
- related tests: `tests/test_body_final_corrections.py`, `tests/test_glossary_integrator.py`, `tests/test_seneca_*`, `tests/test_surgical_polish.py`, `tests/test_us_english_spelling_pass.py`

Reference patch:

- `/tmp/gaiden_worktree_patches/editorial_structure.patch`

## Group F - scripts/corpus/research

Needs branch: `feature/corpus-research-scripts` or a dedicated book_0026 branch, depending on each file.

- `scripts/audit/`
- `scripts/dev/`
- `scripts/migration/`
- `scripts/ops/`
- `scripts/pipeline/`
- `scripts/qa/`
- `scripts/release/`
- `scripts/clean_ocr_russian_revolutionary_tradition.py`
- `scripts/create_pre_marxist_research_corpus_russian_revolutionary_tradition.py`
- `scripts/neutralize_soviet_bias_only_russian_revolutionary_tradition.py`
- `scripts/neutralize_soviet_bias_russian_revolutionary_tradition.py`

Reference patch:

- `/tmp/gaiden_worktree_patches/scripts.patch`

## Group G - egg-info/build artifacts

Resolved separately:

- `gaiden_bookmaker.egg-info/` was removed from the Git index with `git rm --cached -r`.
- `.gitignore` now ignores `*.egg-info/` and `*.egg`.

## Group H - next sprint agents/guidance

Needs branch: `feature/guidance-contract-loader`

- `gaiden/application/lexical/`
- `gaiden/application/ingest/`
- `gaiden/infrastructure/converters/`
- `gaiden/interfaces/cli/`
- `tests/test_lexical_rules_layer.py`
- `tests/test_markitdown_adapter.py`
- `tests/test_markitdown_preprod_service.py`
- `docs/decisions/ADR-storage-canonico-data-root.md`
- `docs/runbooks/project_structure.md`

## Smoke test readiness

Not ready yet.

The internal-agent path is tested, but the worktree still contains broad changes that can contaminate a real smoke test:

- pipeline/web/UI
- legacy split/refine
- storage/env
- editorial/Seneca/glossary
- script migrations

Before the smoke test, either move these groups to branches/stashes/patches or confirm that none of them touches:

- `gaiden/application/agents/translate_router.py`
- `gaiden/application/agents/stage_resolver.py`
- `gaiden/application/agents/stages/modernize_en_us_2026.py`
- `gaiden/infrastructure/openai/responses_client.py`
- `gaiden/application/agents/validators/`
- `data/contracts/`
