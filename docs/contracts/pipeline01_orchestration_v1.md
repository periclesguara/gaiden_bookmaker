# Pipeline 01 Orchestration v1

## Scope
Pipeline 01 (Steps comuns) has a fixed 6-step orchestration for both TXT and HTML lanes.

## Invariants
- UI must expose exactly these numbered steps in order:
  1. Normalize
  2. HeadingCleaner (Mechanical)
  3. Split/Chunk
  4. Translate (script + JSON)
  5. Refine (Aldebaran)
  6. Merge/Finalize
- A step can run only when its prerequisites are satisfied.
- The step order source of truth is backend `build_pipeline01_steps(...)` in `web/pipeline/views.py`.
- `HeadingCleaner` is a fixed mechanical step and must not be removed, replaced by a remote agent, or moved to another position without a new contract version.
- `Split/Chunk` must run after `HeadingCleaner` and must use the cleaned output, not the pre-clean normalized text.
- Any rerun of `Split/Chunk` is a source change event and must invalidate stale downstream artifacts from `Translate`, `Refine`, and `Merge/Finalize`.
- `Refine` runtime contracts must be hardened into explicit `system_prompt` and `user_prompt` rules before execution; raw `instructions` blocks alone are not sufficient.

## Canonical Artifacts
- Source inputs:
  - HTML lane source: `data/md/<book>/<book>_<lang>_source.md`
  - TXT lane source: RAW/TXT input handled by normalize fallback.
- Step 01 Normalize output:
  - `data/normalized/<book>_<lang>_v2.txt`
- Step 02 HeadingCleaner output:
  - `data/chunks/<book>/heading_cleaner/clean.txt`
  - `data/chunks/<book>/heading_cleaner/heading_cleaner_report.json`
- Step 03 Split/Chunk output:
  - `data/chunks/<book>/split_01/*.txt`
- Step 04 Translate outputs (project runtime):
  - `data/translated/<book>/<lang_variant>/*.txt`
  - `data/builds/<book>/<lang>/merge_translate.txt`
- Step 05 Refine outputs:
  - `data/translated/<book>/<lang_variant>/return_aldebaran/*.txt`
  - `data/builds/<book>/<lang>/merge_refine.txt`
- Step 06 Merge/Finalize output:
  - `data/translated/<book>/merge_refine_clean.txt`

## Contracts
- Translate always resolves contract JSON by target language in:
  - `gaiden/contracts/en_modern_2025.json`
  - `gaiden/contracts/en_es_2025.json`
  - `gaiden/contracts/en_ptbr_2025.json`
  - `gaiden/contracts/en_de_krimi_2025.json`
- Refine always runs with Aldebaran runner (`run_aldebaran_refine_return`).

## Definition of Done
- Steps render in fixed order 01..06.
- Buttons enable/disable based on gates.
- Translate is disabled without heading cleaner output, re-chunked `split_01`, and contract.
- Refine is disabled without translate outputs.
- Merge/Finalize is disabled without refine outputs.
- Rechunking removes stale translated/refined outputs so downstream steps cannot silently reuse mismatched chunk files.
- Refine runs only with a hardened runtime contract that explicitly forbids summarization, omission, commentary, and structural drift.
- Tests in `web/pipeline/tests.py` enforce order and key gates.
