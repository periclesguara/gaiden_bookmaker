# Block 01: Qwen normalization and chapter translation

## Scope

New jobs execute this complete chain before editorial production:

```text
RAW → Normalize/Qwen JSON → Split by Chapter → Google Drive
    → chapter returns → validation → merge → BLOCK_01_COMPLETE
```

Frontmatter starts in Block 02. Intake and Normalize must not generate it.
Historical v1/v2 jobs and existing `heading_clean` artifacts remain compatible,
but the v3 path never creates or consumes a new `heading_clean` file.

## Normalize contract

The operator starts Normalize with one explicit POST action. GET requests do
not contact Qwen. The RAW is hashed before extraction and read again afterward;
any byte change aborts the operation. The service segments extracted text into
contiguous blocks and sends untrusted block text to Qwen under
`gaiden_normalize_decision_v2`.

The validator rejects unknown fields, missing decisions, schema or source-hash
divergence, incorrect offsets, unknown enum values, missing evidence,
out-of-range confidence, and headings that are not exact source substrings.
Qwen cannot return replacement prose. The normalized body is assembled only by
concatenating original blocks classified with a `KEEP_*` decision.

Canonical derived artifacts are:

```text
data/normalized/<book_code>/<language>/normalized_body.txt
data/normalized/<book_code>/<language>/normalize-manifest.json
data/normalized/<book_code>/<language>/structure-map.json
```

`REVIEW_REQUIRED` blocks are not silently promoted. They set the Normalize and
structure map to review-required and keep the split blocked.

## Provenance

Normalize updates `Work.source_provenance` to `PROVENANCE_STAGED`. Technical RAW
facts (original filename, SHA-256, bytes, format, MIME, URI, artifact identity,
ingestion time, language, book code, and edition id) are recalculated from the
selected RAW. Deterministic bibliographic suggestions are added only when a
field is empty. Values whose source is `manual` or whose status is `edited` or
`confirmed` are never overwritten by a rerun.

The manifest records the normalizer and contract versions, Qwen model,
correlation id, timestamp, RAW/normalized hashes, each decision, evidence,
warnings, and validation status. Removed source-platform evidence remains in
the byte-identical RAW.

## Split and Drive

The v3 splitter reads only `normalized_body.txt` and `structure-map.json`.
Structure offsets and headings are verified against the normalized hash. Units
must be ordered, nonempty, nonoverlapping, gap-free, and reconstruct the source
exactly. Unreliable maps produce `SPLIT_REVIEW_REQUIRED` and cannot be exported.

Only the requested target language is created below
`04_TRANSLATION_JOBS/<book_code>/<target_language>`. Drive remains transport;
job identity, hashes, state, and canonical artifacts remain in Gaiden.

## Return, merge, and Block 02 gate

Return discovery and validation are progressive and idempotent. Merge remains
blocked until all expected units are `VALIDATED` and none is missing,
conflicting, or rejected. The Gaiden merge follows manifest order and publishes
atomically:

```text
translated_body.txt
translation-manifest.json
qa-report.json
```

The job then enters `BLOCK_01_COMPLETE`. Frontmatter routes and downstream
production actions reject the authoritative flow as soon as Normalize stages
its provenance, and continue rejecting it while any v3 job is incomplete.
Legacy work without staged v3 provenance or a v3 job is not retroactively blocked.

## Deployment and rollback

Apply the additive migrations only after the normal backup and disposable-clone
rehearsal:

```bash
python web/manage.py migrate --plan
python web/manage.py migrate
python web/manage.py check
python web/manage.py makemigrations --check --dry-run
```

The migrations add only a `PipelineArtifact` choice and a job status choice;
they remove no table, column, or historical row. Application rollback may keep
those values in PostgreSQL. Do not delete runtime artifacts or rewrite applied
migrations. Existing v1/v2 jobs remain the operational rollback path.
