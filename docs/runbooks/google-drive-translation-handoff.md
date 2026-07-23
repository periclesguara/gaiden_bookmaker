# Google Drive translation handoff

## Directory and language contract

All new jobs use this one tree:

```text
04_TRANSLATION_JOBS/
└── <book_code>/
    └── <target-language>/
        ├── input/
        ├── return/
        └── superseded/<job_id>/input/
```

External identifiers use hyphens: `en-us`, `pt-br`, `fr`, `de`, `es`, `it`.
Legacy internals that require underscores call the single conversion helper.

The input TXT is always the non-empty UTF-8 HeadingCleaner output:

`<book_code>_<frozen_title_slug>_heading_clean_<language>.txt`

Return names declare their real stage:

- intermediate: `<book_code>_<slug>_translated_<language>.txt`
- final: `<book_code>_<slug>_official_<language>.txt`

## Immutable manifest

Export creates one database TranslationJob, a local manifest, and the same
manifest in `input/`. The v2 manifest freezes `job_id`, Edition, IntakeItem,
book code, title/slug, languages, output stage, input filename/SHA, and expected
return folder/name. The operator returns the manifest beside the TXT. Final
external editorial returns must add `completed_stages` containing
`translation`, `refine`, and `polish`; frozen fields may not change.

An identical input SHA reuses the job. Changed input creates a new job and marks
the previous one superseded. Implementations archive old remote inputs before
reusing the deterministic current filename.

## Validation

Return discovery is exact and fail-closed. One TXT and one manifest must exist
as direct children of the frozen return folder. Validation checks identity,
UTF-8, non-empty regular content, SHA, non-identity with input, configurable
size bounds, headings, protected markers, and detected language. A persistent
report records `PASS`, `WARNING_REQUIRES_CONFIRMATION`, or `FAIL`. Only `PASS`
is processed automatically. A warning remains in the job-specific pending
directory until an authenticated editor submits a non-empty justification in
the steps page. The actor, justification, timestamp, job ID, and return SHA are
recorded in the TranslationJob and validation report. `FAIL` never creates a
processable pending return and can never alter the official body.

`translated` returns enter the internal Refine → Merge Refine → Polish path.
They cannot be promoted directly. `official` returns use the official-body
promotion service after the manifest confirms the external editorial stages.

Configure rclone locally (never in Git):

```bash
rclone config
rclone lsd gaiden_drive:01_INBOX_RAW
rclone lsd gaiden_drive:04_TRANSLATION_JOBS
```
