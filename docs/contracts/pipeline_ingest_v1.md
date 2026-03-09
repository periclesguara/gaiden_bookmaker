# Pipeline Ingest Contract v1

- `contract_name`: `pipeline_ingest_v1`
- `contract_version`: `1.0.0`

## Gate 0 Endpoint

- `GET /pipeline/editions/edit/` (`book_edition_new`) is the canonical opening page for new books.
- `POST /pipeline/editions/edit/` (`book_edition_new`)

## Canonical Entry Flow

- New book intake must start at `book_edition_new`.
- This cadastro page is the fixed entrypoint for both TXT and HTML lanes.
- For `source_format=html`, the next fixed page is the HTML lane dashboard.
- The HTML conversion stages already behind that dashboard are considered stable project surface and must not be silently reordered, bypassed, or replaced from another entrypoint.
- Any incompatible change to this opening flow requires a new contract version.

## Canonical Input Fields (required)

- `book_code` (`string`)
- `language` (`string`)
- `title` (`string`)
- `author_name` (`string`)
- `publication_year` (`int`, default fallback `2026`)
- `source_format` (`html|txt`)
- `source_file` (`file`)

## Backward-Compatible Input Aliases

- `book_id` or `book` -> `book_code`
- `lang` -> `language`
- `author` -> `author_name`

## Validation Rules

- Reject invalid `source_format` with `400`.
- `source_file` extension must match lane:
  - `html`: `.html` or `.htm`
  - `txt`: `.txt`
- Do not proceed to ingest stage when validation fails.

## Persistence Contract (success path)

- `BookEditionTemplate` must be saved with:
  - `book_code`, `language`, `text_source_mode`
- `EditionPipeline` must exist and set deterministic initial stage:
  - `HTML_UPLOADED` for HTML lane
  - `TXT_UPLOADED` for TXT lane
- Editorial `Edition` auto-create requires resolvable `Work` by `book_code`.
  - If `Work` does not exist, fail with user-facing message.

## Artifact Paths (deterministic)

- HTML upload:
  - `data/raw/<book_code>/<book_code>_<lang>_raw.html`
- TXT upload:
  - `data/raw/<book_code>/<book_code>_<lang>_raw.txt`

## Redirect Contract (deterministic)

- `source_format=html` -> `302 /pipeline/html/<edition_id>/`
- `source_format=txt` -> `302 /pipeline/editions/<edition_id>/steps/`

## UI Contract

- The cadastro form template is `web/pipeline/templates/pipeline/book_edition_form.html`.
- It must expose stable contract markers for regression tests:
  - `data-contract="pipeline_ingest_v1"`
  - `data-contract-entrypoint="book_edition_new"`
  - `data-contract-html-next="pipeline_html_dashboard"`
- The page must continue to show the HTML/TXT choice and the hint that HTML proceeds to `Pre-producao HTML`.

## Regression Tests Required

- `test_cadastro_get_is_canonical_entrypoint_for_new_html_books`
- `test_cadastro_redirects_to_html_dashboard_when_source_format_is_html`
- `test_cadastro_redirects_to_common_pipeline_when_source_format_is_txt`
- `test_editorial_autocreate_requires_existing_work`
- `test_cadastro_accepts_backcompat_post_field_names`

Any incompatible change must be introduced as `pipeline_ingest_v2` without silently breaking v1.
