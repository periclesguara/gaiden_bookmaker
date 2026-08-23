# Source provenance and canonical Title Page

## Scope

Gaiden stores stable facts about an imported source once per `Work` in
`Work.source_provenance`. Intake, the Manual editor, frontmatter export, and
book builds use this same record. It is separate from edition metadata:
extraction must never overwrite an editorial title, author, adapter,
translator, or edition publication year.

The canonical editorial name is **Title Page**. `frontispiece_template` and
`frontispiece.md` remain compatibility surfaces for existing database state,
bookmarks, and older tooling. A frontmatter export writes `title_page.md` and a
byte-identical `frontispiece.md` alias.

## Provenance schema

The JSON record may contain:

- `original_title` and `source_author`;
- `original_publication_year` and `original_publication_basis`;
- `source_platform`, `source_identifier`, and canonical `source_url`;
- `source_release_date`, distinct from original publication and edition dates;
- `source_credits`, `rights`, `source_language`, and `subjects`;
- `source_filename` and the SHA-256 of the original uploaded bytes;
- `extraction_warnings` when operator review is required.

The extractor intentionally omits volatile or technical facts such as download
counts, reading levels, “most recently updated”, and `dcterms:modified`. It
does not store manuscript text. Digitization/transcription contributors are
shown as **Source credits**, never as authorship.

## Intake and Manual behavior

Only an explicit RAW upload POST calls the deterministic EPUB/TXT/Markdown
extractor. Page GETs do not inspect files and do not call Qwen. The RAW bytes
are saved unchanged; the SHA-256 is computed from those bytes. An extraction
failure still records the original filename, hash, and a review warning.

The Manual editor can correct bibliographic values. Source filename and SHA-256
are disabled fields and cannot be changed through that form. Saving the shared
record does not mutate any edition field.

Normalize may copy `source_provenance` into its JSON audit report. The report
contains only the stable record and deletion coordinates; it never contains
removed source text.

## Frontmatter and build order

When provenance exists, export produces this order:

1. `title_page.md`;
2. `copyright.md`;
3. `source_record.md` headed `Original Source Record`;
4. `about_edition.md`, when configured;
5. `about_contributor.md`, when configured.

Builders prefer `title_page.md` and fall back to `frontispiece.md` only for
legacy exports. `source_record.md` is optional for old works but, when present,
is always assembled after copyright and before edition/contributor notes.

## Migration and recovery

Migration `editorial.0024_work_source_provenance` adds one nullable-in-practice
JSON object field with `default=dict`; it removes or rewrites no existing data.
Before applying it to a persistent database, follow the repository migration
policy: create and verify a backup, rehearse on a disposable PostgreSQL 16 plus
pgvector database, review `migrate --plan`, and obtain operator authorization.

Rollback of application code is safe while the column remains present. A
forward repair should be used if the schema ever needs further adjustment;
do not delete provenance from a persistent environment as part of rollback.
