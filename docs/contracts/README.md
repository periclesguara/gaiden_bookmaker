# Contracts Index

## Pipeline
- **pipeline_ingest_v1** — Gate 0 fixed entrypoint: cadastro is the canonical opening page for new books, including the stable handoff into the HTML lane
- **pipeline01_orchestration_v1** — Pipeline 01 fixed steps: Normalize → HeadingCleaner (mechanical) → Chunk → Translate → Refine → Merge/Finalize
- **editorial_image_pipeline_v1** — Mandatory editorial image flow in `edition_steps`, with fixed control order and fixed `Insert image placeholders` position inside `#transformacao-editorial`

## Stage 01 Translation Governance
- Active canonical controlled modern English Stage 01 contract: `gaiden/contracts/en_modern_2025.json`
- Active canonical `contract_name`: `stage01_modern_translation_controlled_v3`
- Archived inactive predecessor: `gaiden/contracts/archive/en_modern_2025_v2.json`
- Audit record: `docs/contracts/audit/stage01_contract_supersession_2026-03-20.json`
