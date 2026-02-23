# Projects Source of Truth Policy (2026)

## Non-negotiable contract
- `CANONICAL_INPUT = RAW` and must be immutable after ingest.
- `CANONICAL_OUTPUT = TRUTH_FINAL` and must be immutable once frozen.
- Database is a golden index of pointers/hashes/status only. DB is not the canonical text file.

## Canonical directory contract
- RAW: `data/raw/<book_id>/<lang>/source.(txt|md)`
- TRUTH_FINAL: `data/books/<book_id>/<lang>/<book_id>_refine_clean.md`
- RUN AUDIT: `docs/audit/runs/<book_id>_{ingest|freeze}_<timestamp>/`

## Operational flow
1. Register edition metadata (`REGISTERED`).
2. Upload RAW to Django storage (`UPLOADED`).
3. Materialize RAW to canonical filesystem path (`INGESTED`).
4. Run pipeline stages (normalize/chunk/translate/refine/polish).
5. Freeze canonical truth with receipts (`CANONICAL_READY`).

## Gates
- `NORMALIZE` and `CHUNK` are blocked when status is below `INGESTED`.
- `Freeze Canonical` is blocked if no final text source can be resolved.

## Mandatory receipts
- Ingest run must contain at least: `git_head.txt`, `git_status.txt`, `SHA256SUMS.txt`, `manifest.json`.
- Freeze run must contain at least:
  - `git_head.txt`, `git_status.txt`
  - `SHA256SUMS.txt` (truth)
  - `images_list.txt`, `images_SHA256SUMS.txt` (when images exist)
  - `cover_list.txt`, `cover_SHA256SUMS.txt` (when cover exists)
  - `manifest.json`

## Scope boundaries
- Projects: registration + RAW upload.
- Edition Steps: manual materialize/freeze gates.
- Runner Matrix: execution only after ingest gate is satisfied.
