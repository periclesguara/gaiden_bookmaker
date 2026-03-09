# Contracts Index

## Pipeline
- **pipeline_ingest_v1** — Gate 0 fixed entrypoint: cadastro is the canonical opening page for new books, including the stable handoff into the HTML lane
- **pipeline01_orchestration_v1** — Pipeline 01 fixed steps: Normalize → HeadingCleaner (mechanical) → Chunk → Translate → Refine → Merge/Finalize
- **editorial_image_pipeline_v1** — Mandatory editorial image flow in `edition_steps`, with fixed control order and fixed `Insert image placeholders` position inside `#transformacao-editorial`
