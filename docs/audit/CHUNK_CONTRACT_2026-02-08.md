# Chunk Contract (2026-02-08)

## Canonical Paths
- NORMALIZED input: `data/normalized/<book_code>/en/<book_code>_en_v2.txt`
- CHUNKS dir: `data/chunks/<book_code>/en/`
- CHUNK file pattern: `ch_<chapter_id:03d>_chunk_<idx:03d>.txt`
- MANIFEST: `data/chunks/<book_code>/en/chunks_manifest.json`
- RUN REPORT: `data/chunks/<book_code>/en/chunk_run_report.json`

## Language / Mode
- Chunking is EN-only and shared across target languages.
- `lang` must be `en` internally.

## Heading / Chapter Detection (Anti-False-Positive)
Reliable headings only:
1) Keyword lines: `CHAPTER`, `PART`, `BOOK` (case-insensitive)
2) Markdown headings: `# Title` (one or more #)
3) Roman numeral with dot: `I. Title`, `IV. Title`
4) Roman-only line followed by title on next line

Prohibited patterns:
- `^\d+\s*-\s*` (false positives like `500 - ...`)
- `^\d+\s*[\.-]\s+` without keyword

Isolation rule:
- Non-keyword headings must be isolated (blank line before and/or after).
- Keyword headings do not require isolation.

If no headings are detected:
- SINGLE_CHAPTER_MODE
- chapter_id=1
- heading_line=""
- start_line_idx=0, end_line_idx=EOF

## Content Preservation
- No content may be dropped.
- Preamble before first heading must be attached to chapter 1.

## Chunking Rules
- target_tokens = 1500
- max_tokens = 2000 (hard cap)
- Estimator:
  - tiktoken (cl100k_base) when available
  - fallback: chars/4 (record as `chars4`)
- Chunk never crosses chapter boundaries.
- Chunk never merges across chapters.
- Small chunks are allowed; do not merge last chunk.
- Oversize paragraph split order:
  1) double newline
  2) newline
  3) sentence boundary (. ! ?)
  4) hard split by chars

## Chapter IDs
- chapter_id is sequential (1..N) based on detected order.
- Heading numbers are metadata only.

## Manifest v2 (Identity)
`chunks_manifest.json` must include:
- schema_version = `chunks_manifest_v2`
- book_code
- lang
- normalized_path
- normalized_sha256
- chunker_version
- created_at (ISO)
- config: target_tokens, max_tokens, estimator
- headings_detected_count
- single_chapter_mode
- chapters[]:
  - chapter_id
  - heading_line
  - heading_number (optional)
  - start_line_idx / end_line_idx (0-based)
  - chunk_count
  - chunks[]:
    - chunk_id
    - file_path
    - sha256
    - token_estimate
    - char_count
    - start_line_idx / end_line_idx

`chunk_run_report.json` must include:
- cwd
- base_data_dir
- resolved paths
- effective config
- headings list
- single_chapter_mode
- checks PASS/FAIL with reasons

## Audit Checks
- FAIL if normalized missing or empty
- FAIL if manifest lacks normalized_sha256
- FAIL if coverage does not include all lines (no line loss)
- FAIL if any chunk > max_tokens
- FAIL if chunk crosses chapter bounds or has mismatched chapter_id
- WARN if estimator = chars4
- FAIL if char coverage (covered_chars vs normalized_chars) is below tolerance
- Char coverage tolerance: max(1% of normalized_chars, 1000 chars)
