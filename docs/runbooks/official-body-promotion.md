# Official body promotion

The only official body authority is:

`data/editions/<edition_id>/core/miolo_oficial.txt`

All consumers—frontmatter, preview, internal images, Markdown build, EPUB, and
PDF—resolve this file through the active, SHA-verified OfficialBodySnapshot.
`core_last.txt` is not an independent authority. `EditionPipeline.core_last_txt_path`
stores the canonical path relative to the configured storage root.

## Valid provenance

- `internal_polish`: validated internal Polish output.
- `drive_official`: v2 Drive job with `output_stage=official` and either a PASS
  report or an explicitly confirmed and audited warning.
- `manual_editorial_approval`: explicit supported editorial promotion.

Block 02 completes when one active official snapshot exists and both its
immutable version and `miolo_oficial.txt` match the recorded SHA. It therefore
supports the internal flow, Drive intermediate flow followed by internal
Polish, and Drive final flow. Block 03 remains locked otherwise.

## Recoverable protocol

Promotion obtains row locks and records:

`PREPARED → FILE_STAGED → DB_COMMITTED → CANONICAL_PUBLISHED → COMPLETED`

Every version is preserved at:

`data/editions/<edition_id>/core/versions/<timestamp>_<sha>.txt`

Writes use a temporary file, flush/fsync, and atomic replace. A partial failure
after database commit is recovered from the immutable version:

```bash
python web/manage.py reconcile_official_body
```

The database enforces at most one active snapshot per edition and one snapshot
per edition/SHA. Re-promoting the active SHA is a no-op. Pending Drive files are
deleted only after `COMPLETED`.
