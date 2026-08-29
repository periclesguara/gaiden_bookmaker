# Gaiden architecture

## Active application

Gaiden has two deliberate layers:

- `gaiden/` contains reusable editorial processing code and command-line tools;
- `web/` contains the Django operator application.

The Django project is `web/gaiden_portal` and installs two first-party apps:

- `editorial`: canonical works, editions, contributors, metadata, frontmatter,
  and editorial artifacts;
- `pipeline`: jobs, runs, source selection, build paths, and operator flows.

Reusable business logic belongs in service modules. Views should coordinate
HTTP input and output rather than implement filesystem or editorial rules.

### Automated Intake Drive browsing

The Drive Intake folder picker opens the selected `01_INBOX_RAW` folder with a
lightweight, non-recursive file listing so operators can verify the files before
requesting an import preview. Browsing does not download content, allocate book
codes, or write database state. The subsequent preview performs the full
discovery (optionally recursive), and file transfer still requires explicit
confirmation.

After a Drive folder reaches `REGISTERED`, the Intake page links that folder to
its batch-filtered file table. Each supported file exposes a bounded reading
preview for TXT, HTML, or EPUB plus its current production stage. The preview
downloads into temporary staging, verifies the registered SHA-256, extracts
only display text, and removes staging on exit. It does not create an edition,
canonical extraction artifacts, audit events, or production metadata. Sending
the file to the editing workflow remains a separate explicit POST action.

### Intake provenance schema compatibility

Automated Drive Intake creates every `Work` with an explicit empty
`source_provenance` object. The compatibility migration adds that JSON column
only when it is absent, initializes pre-existing rows with `{}`, and preserves
the physical column on reverse migration. This keeps the active Intake safe on
both clean databases and environments where an approved provenance migration
already created the column; it never makes provenance nullable or replaces an
existing record.

### Intake image and frontmatter rendering contract

The post-Intake workflow stores internal illustrations under the canonical
edition image directory. Numbered filenames are mapped deterministically to
chapter order during premium rendering (`01` to chapter 1, `02` to chapter 2,
and so on); an explicit image already present in a chapter takes precedence.
Unnumbered images fill the remaining unillustrated chapters in natural filename
order. Images that cannot be mapped are reported as render warnings rather than
silently inserted in an unrelated chapter.

Editorial frontmatter fields accept Markdown formatting. Premium rendering
converts headings, emphasis, lists, and block quotes to XHTML while escaping raw
HTML. Markdown control characters must not remain visible in the preview or the
packaged EPUB.

Premium EPUB reading order is canonical and shared by the OPF spine, visible
preview, and post-Intake workflow: cover, title page, frontispiece when present,
copyright, contents, About This Book, body matter, and The End. The renderer
audit rejects a package whose frontmatter diverges from this order.

When an approved `BOOK.MD_FINAL` exists, the premium renderer uses it before a
legacy `kdp_merged.md` fallback. This prevents stale source material from
overriding the current editorial manuscript in preview or EPUB output.

The premium audit blocks Project Gutenberg boilerplate in rendered chapter
content. A concise source reference in the explicit copyright frontmatter is
permitted so public-domain provenance can remain visible to readers.

The renderer also recognizes a contiguous set of standalone Roman-numeral
headings as chapter boundaries. This keeps approved manuscripts whose chapters
are headed `I` through `LXI` from being collapsed into surrounding metadata.

### Canonical chapter-heading contract

For EPUB intake, a semantic chapter `section` (identified as `chapter-N` or
marked as a chapter) with an ordinal-only heading is canonicalized to an
explicit `CHAPTER N` heading before the plain-text artifact is made. This
preserves the EPUB's structural chapter boundary rather than guessing from a
standalone Roman numeral in prose. When explicit chapter headings are present,
normalization does not promote standalone markers, and the Heading Cleaner only
deduplicates consecutive headings; a repeated chapter number after body text
remains a valid Part/Book restart. Chapter splitting then validates coverage by
reconstructing the UTF-8 source from its generated units before a Drive export
can be enabled. Bare words such as `Introduction`, `Preface`, or `Appendix`
are accepted as splitter headings only when they are Markdown headings or are
title-cased/all-caps and bounded by blank lines; a common word split out of
prose cannot create a false EPUB section.

### Consolidated translation import

Post-Intake Step 4 accepts one operator-supplied consolidated TXT or Markdown
manuscript and promotes it directly to the target edition's translation
artifact. The chapter split remains the Drive transport contract, but Step 4
does not discover per-chapter returns, validate them individually, or merge
them. The importer keeps only storage-safety checks: an allowed upload type,
size limit, non-empty content, and valid UTF-8. It records the imported file's
SHA-256 and audit event, then advances the target pipeline to `TRANSLATED`.

An optional, edition-owned `epub_metadata.json` in the build directory may add
validated EPUB language, publisher, rights, date, description, subjects,
contributors, and per-filename illustration alt text. Its schema is
`gaiden_epub_metadata_v1`; malformed or unsupported fields stop rendering.
This file is operator runtime configuration, not a Git artifact. The renderer
uses the language override in XHTML and OPF, emits the supplied Dublin Core
metadata, and uses the saved-image descriptions only for the matching filename.

### Final EPUBCheck gate

`gaiden.application.builds.epubcheck_service` is the only final-publication
EPUBCheck runner. It accepts only a `.epub` within Gaiden storage, invokes the
configured executable as an argument list (never through a shell), records
stdout, stderr, exit code, tool version, duration, SHA-256, and fatal/error/
warning counts, and writes `*.epubcheck.json` next to the final artifact.

The settings are `EPUBCHECK_ENABLED`, `EPUBCHECK_EXECUTABLE`, and
`EPUBCHECK_TIMEOUT_SECONDS` (default 120). EPUBCheck is provisioned by the
host or image; the application never downloads it at runtime. A missing binary
or timeout is a fail-closed status, never an approval.

`EditionBuild` keeps the persistent gate state: `EPUBCHECK_PENDING`,
`EPUBCHECK_RUNNING`, `EPUBCHECK_PASSED`, `EPUBCHECK_PASSED_WITH_WARNINGS`,
`EPUBCHECK_FAILED`, or `EPUBCHECK_UNAVAILABLE`. `DONE`/`FINAL_READY` and the
final-download endpoint require a pass with zero fatal and error counts, a
persisted report, and a validated EPUB SHA-256 equal to the downloadable file.
Warnings are retained and visible but do not block publication. If bytes change
after a pass, the next final-download decision invalidates the gate, returns the
build to `EPUBCHECK_PENDING`, and blocks download until a new run succeeds.

The post-Intake page exposes `Salvar EPUB final` at the end of the pipeline.
It is disabled unless the exact EPUB is a registered final build with a current
EPUBCheck pass; confirming it records an idempotent final-save audit event.
The same page has an `Início` link at the top for return to the main Gaiden page.

### Writer engine phase 1

`gaiden/writer_engine/` is a reusable, UI-independent draft-generation layer.
It owns deterministic corpus discovery and chunking, atomic vector-index
serialization, retrieval, prompt boundaries, Qwen client coordination, and
originality checks. It does not own canonical promotion or final builds.

Generation and embeddings use separate configurable OpenAI-compatible local
services. Qwen3.5-9B is the default generation model and
Qwen3-Embedding-0.6B is the default retrieval model. Corpus files, model
weights, indexes, requests, drafts, and audit sidecars remain external runtime
artifacts. The reusable engine remains independent of Django. The `writer`
application opens without Django authentication and adds source manifests,
story projects, creative bibles, chapter parameters, immutable generation
sessions, and explicit editorial finalization.
Its external file-treatment and deployment contract is defined in
`docs/writer-workflow.md`.

## Runtime data boundary

Git is the source-of-truth for code, migrations, tests, small fixtures,
configuration templates, and documentation. It is not the canonical store for:

- manuscripts or chunks;
- generated EPUB, PDF, Markdown, images, or covers;
- databases, backups, logs, credentials, or operator exports.

Runtime artifacts belong under the configured external storage. Paths under
`data/` are compatibility locations and must remain ignored unless a small,
reviewed fixture is intentionally added under a dedicated fixture directory.

## Compatibility inventory

The following names indicate compatibility code, not automatic deletion
candidates:

- modules suffixed `_2025.py`;
- `setup_translate_2025.sh`;
- book-specific build and normalization scripts;
- `web/gaiden_portal/settings_sqlite.py`.

Before removal, each item must have its callers searched, its replacement
identified, and a rollback commit recorded. New production paths must not add
dependencies on these compatibility modules.

## Quality gates

Every pull request must pass:

1. repository hygiene and current-tree secret scanning;
2. Python and shell syntax checks;
3. PostgreSQL 16 plus pgvector clean migration;
4. migration drift and pending-migration checks;
5. at least the protected Django test baseline;
6. dependency vulnerability auditing.

The minimum test count is a regression guard, not a coverage target. New or
changed behavior must add direct tests.

## Change boundaries

Migration repair, runtime-data cleanup, compatibility-code retirement, and
feature integration are separate review units. Persistent data is never
deleted merely to make model state match source code.
