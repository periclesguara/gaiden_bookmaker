# CHUNK Action Contract (2026-02-06)

## Purpose
Generate deterministic EN chunks for translation, preserving chapter boundaries and token limits.

## Inputs
- Normalized EN text
  - Primary: `data/normalized/<book_code>/EN/<book_code>_en_v2.txt`
  - Compatible fallback: `data/normalized/<book_code>_en_v2.txt`
  - Optional: `*_en_v1.txt`

## Outputs (filesystem)
- Chunk directory:
  - `data/chunks/<book_code>/en/`
- Files:
  - `ch_<chapter:02d>__p_<part:02d>.txt`
  - `ch_<chapter:02d>_chunk_<part:03d>.txt` (translation compatibility mirror)
  - `manifest.json`

## Guards / Preconditions
- Normalized text must exist, or Runner will auto-normalize first.
- EN only (current scope).

## Determinism
- Same input => same manifest + filenames.
- No reordering of paragraphs.

## No-cross-chapter Rule
- Each chunk is fully contained inside a single chapter.
- Preamble (before first chapter) is stored as `ch_00__p_XX`.

## Max Token Rule
- Estimated tokens: `ceil(len(text)/4)`
- Each chunk must be `<= 1500` estimated tokens.
- Oversize paragraphs are split by sentences; fallback to fixed-size splits.

## Logging Expectations
Runner logs must include:
- Chapters detected
- Total chunks
- Max estimated tokens
- Oversize split count
- Output directory

## Idempotency
- If `manifest.json` exists and `skip_existing` is ON, the item is SKIPPED.
- If overwrite is allowed, old `ch_*.txt` + `manifest.json` are removed before re-run.
