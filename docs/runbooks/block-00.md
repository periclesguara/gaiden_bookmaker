# Block 00 — Google Drive Intake

Block 00 registers source books before the production pipeline. The home page
keeps three independent entries: Google Drive Intake, individual upload, and
collections.

## Boundary

- Intake Drive root: `gaiden_drive:01_INBOX_RAW`.
- Only direct child folders may be selected.
- Folder names, file names, and stored relative paths reject traversal,
  absolute paths, backslashes, and alternate remotes.
- Dashboard and status GET requests never call rclone, download files, create
  records, repair state, or update timestamps. Refreshing the Drive folder cache
  is an explicit CSRF-protected POST.
- TXT, HTML, and EPUB sources are supported; image files are reported and left
  for the editorial image stage.

## State and identity

The normal path is:

`DISCOVERED → DOWNLOADING → DOWNLOADED → CLEAN_READY → BOOKMAKER_HANDOFF`

Interrupted downloads and artifact conflicts are explicit reconciliation/error
states. Reconciliation is previewed by GET and applied only by confirmed POST.

SHA-256 deduplication is repository-wide. Duplicate rows point to the earliest
canonical item and cannot create a Work, Edition, or TranslationJob. `book_code`
and `handoff_edition_id` have conditional unique constraints. Empty book codes
are allocated as `book_NNNN` under a PostgreSQL transaction/advisory lock.

The Bookmaker handoff locks the IntakeItem, reuses an existing complete handoff,
and never reruns source extraction on a second click. Missing records or files
produce a closed `Partial Bookmaker handoff` error instead of silently creating
a second identity.

## Operator checks

```bash
python web/manage.py check
python web/manage.py makemigrations --check --dry-run
python web/manage.py test intake_module
```
