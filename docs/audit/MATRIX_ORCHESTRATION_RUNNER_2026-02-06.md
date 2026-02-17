# Matrix Orchestration Runner (Phase 1)

Objective
- Provide a queue-based Runner UI (1-by-1) to orchestrate pipeline actions without altering core logic.

How to Use (MVP: TRANSLATE)
1) Open `/pipeline/runner/`.
2) Select one or more books.
3) Select one or more languages.
4) Action: TRANSLATE.
5) Options:
   - Skip existing (default ON)
   - Stop on error (default OFF)
   - Dry-run (default OFF)
6) Click Run and refresh the run detail view.

Rules
- Queue is sequential (no parallel jobs).
- Skip existing: if `merge_translate_<LANG>.txt` already exists, item is SKIPPED.
- Stop on error: when enabled, remaining items are marked SKIPPED.
- Dry-run: no execution; items are marked SKIPPED with log `DRY-RUN`.

Outputs
- Translations are written to the standard location:
  `data/translated/book_XXXX/<LANG>/merge_translate_<LANG>.txt`

Notes
- Core pipeline logic is unchanged; the runner only orchestrates calls.
- Future actions (SPLIT/RETURN_REFINE/BUILD) will reuse the same queue model.
