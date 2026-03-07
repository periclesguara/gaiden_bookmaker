# Contracts Index

## Pipeline
- **pipeline_ingest_v1** — Gate 0 (cadastro + upload + lane routing)
- **pipeline01_orchestration_v1** — Pipeline 01 fixed steps: Normalize → Chunk → HeadingCleaner → Translate → Refine → Merge/Finalize
- **editorial_image_pipeline_v1** — Mandatory editorial image flow in `edition_steps`, with fixed control order and fixed `Insert image placeholders` position inside `#transformacao-editorial`
