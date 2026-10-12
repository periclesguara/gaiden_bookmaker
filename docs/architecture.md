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

### Writer provenance and Intake asset lane

Ordinary editions retain the existing editorial workbench. A closed Writer
canonical is routed to a dedicated Intake asset lane for frontmatter, cover,
internal images, and premium preview. The canonical miolo is copied with its
digest intact and does not pass through Normalize, Heading Cleaner, chapter
split, translation, export, or import. Backend guards redirect direct
transformation requests to the dedicated Intake lane.

### Closed Writer to Intake handoff

For a `CLOSED` Writer project with a canonical revision, the Writer detail page
offers the explicit **Prosseguir para Intake** action. The operator supplies
the required author credit and publisher/imprint; no author is inferred from
the manuscript.  The bridge validates the project state, revision ownership,
canonical file, manifest identity, and matching SHA-256 values before creating
or reusing a `Work` and `Edition` for the downstream Intake workflow.

The bridge stores `origin: writer_canonical`, Writer project/revision IDs, and
the source SHA-256 in `Work.source_provenance`, assigns the operational Intake
code, and registers the edition in the Intake asset lane. It makes a byte-preserving,
atomic copy at `data/builds/<work-code>/<language>/BOOK.MD_FINAL`, records that
copy's SHA-256, and initializes the existing `EditionPipeline` at `FINAL_MD`.
It never creates a RAW artifact or runs Normalize, Chunk, Heading Cleaner,
Translate, Refine, Polish, or Merge.  Repeating the action for the same Writer
project and revision reuses the same Edition; a divergent existing final body
is rejected rather than overwritten.

After all Writer chapters reach `FINAL`, **Salvar manuscrito** creates the
reviewed revision. Closing the project selects the canonical revision and
enables the direct Intake handoff. The final miolo does not use a book-level
Google Drive export/import round trip.

### Intake image and frontmatter rendering contract

The visual book preview is shared by Intake and the Writer-canonical
production view. `editorial.services.book_preview.build_book_preview_context`
owns the `EditionRenderer` context, page navigation, chapter counts, cover
placeholder/cover resolution, and local image assets; both views render the
existing `editorial/premium_epub_preview.html` and its asset endpoint. Writer
production supplies `BOOK.BUILD.MD`, falling back to `BOOK.MD_FINAL`, as the
explicit renderer source. This creates preview XHTML/assets only; it does
not package an EPUB, run Pandoc, or run EPUBCheck.

The post-Intake workflow stores internal illustrations under the canonical
edition image directory. Numbered filenames are mapped deterministically to
chapter order during premium rendering (`01` to chapter 1, `02` to chapter 2,
and so on); an explicit image already present in a chapter takes precedence.
Unnumbered images fill the remaining unillustrated chapters in natural filename
order. Images that cannot be mapped are reported as render warnings rather than
silently inserted in an unrelated chapter.

Source EPUB artwork is never copied into the canonical editorial image
directory and inline source image references are removed during extraction.
Only images explicitly uploaded through the editorial image step are eligible,
and that upload requires the operator to confirm that every file was generated
for the current edition rather than inherited from the source work.

Editorial frontmatter fields accept Markdown formatting. Premium rendering
converts headings, emphasis, lists, and block quotes to XHTML while escaping raw
HTML. Markdown control characters must not remain visible in the preview or the
packaged EPUB.

New frontmatter records leave publisher, seal, frontispiece, and copyright
empty in the editor and preview. Saving an intentional blank does not restore a
publisher or localized template. Publisher and seal choices remain available,
and localized defaults are inserted only through the operator's explicit
``apply_defaults`` action. The ``{publisher}`` placeholder also remains empty
when no publisher was selected; preview rendering never invents a brand.

When an edition has no official cover, the premium preview remains readable by
rendering a clearly marked internal placeholder. Preview approval and EPUB
packaging stay blocked until an edition-owned cover is saved under
`data/covers/<book>/<language>/`; the placeholder is never a production cover.
In the post-Intake workflow, selecting a cover starts its upload immediately;
the interface remains in an explicit sending state until the server redirects
back with the persisted cover status.

### Copyright holder safety

Default copyright frontmatter attributes the modern edition to `{publisher}`;
the original author remains identified only in the work metadata. The forward
repair migration `pipeline.0023_replace_hard_coded_copyright_holder` replaces
the erroneous Arthur Conan Doyle/Doyke holder text in existing production
templates and synchronized edition frontmatter. It changes only the copyright
fields and is intentionally irreversible; operators must use the verified
database backup made before migration if recovery is required.

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

### Final print-production boundary

`gaiden/print_engine/` is the isolated final-production layer for physical PDF
artifacts. It consumes approved editorial content after the final EPUB gate and
does not import Django, `web.writer`, or `gaiden.writer_engine`. Writer owns text
creation only; it cannot compile, package, or preflight print artifacts.

The standalone `python -m gaiden.print_engine` command renders and compiles `INTERIOR.pdf`,
reports its final even page count, and preflights the matching `COVER.pdf` after
the provider-derived spine width is supplied. Generated TeX/PDF files stay in
external runtime storage. Print validation runs in its own CI workflow and does
not require a database, Writer service, model endpoint, or editorial source
corpus.

`web/printbook/` is the operator-facing persistence adapter around that core.
Its internal catalog exposes only integrity-checked final `DONE` EPUB builds.
Intake links open a source dashboard inside PrintBook; creating a physical
edition is an explicit POST. Its independent EPUB preview reads the approved
ZIP and uses PrintBook templates and sandboxed asset routes, without calling
the editorial renderer or changing approval state.
It accepts only a current, integrity-checked final `EditionBuild`, freezes its
official-body SHA-256 in `PrintEdition`, and records versioned interior, cover,
preflight, and export rows. All generated files remain in external Gaiden
storage. Source/configuration/hash drift invalidates dependent builds without
deleting history. The adapter imports no Writer model and never writes to the
editorial source.

The first platform profile is IngramSpark paperback, black-and-white interior,
with explicit supported trims and paper choices. CoverMaker may create a DRAFT
proof using a visibly identified paper-caliper estimate, but production cover
geometry depends on the final PDF page count and an official-template spine
width. A separate
administrator-provisioned validator supplies hash-bound PDF/X, ICC, image,
page-box, safe-area, template, barcode, and font-license evidence. Missing or
incomplete evidence is a release blocker, never a simulated pass.

### Writer engine and canonical Intake handoff

`gaiden/writer_engine/` is a reusable, UI-independent draft-generation layer.
It owns deterministic corpus discovery and chunking, vector-index serialization,
retrieval, prompt boundaries, local model coordination, and originality checks.
It does not own editorial production or print specifications.

All model-backed stages use the configured loopback Ollama service through
`GAIDEN_LOCAL_MODEL_URL`; generation and embeddings use separately configured
local model names. Remote endpoints are rejected. Qwen and Phi model families
are rejected by the runtime. Legacy OpenAI-named adapter imports route only to
the same local service and never instantiate a cloud SDK.

Closing a Writer project freezes the selected canonical manuscript and records
its digest. That exact manuscript is registered as the Intake text source. Once
it reaches Intake, the miolo is not normalized, heading-cleaned, split,
translated, exported, or imported. Intake remains responsible for frontmatter,
images, cover, and the approval preview using its own templates. Writer's final
review screen links directly to that dedicated Intake asset lane.

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

### Read-only Intake and Writer library navigation

`web.intake.services.catalog` projects registered sources and existing editorial
editions into one row per Work for the `/intake/` landing page. PDF and explicitly
classified reference material stays in the Writer library rather than the
book-title catalog. `web.writer.services.library` exposes IntakeBatch/IntakeItem
relationships and all local source-root file formats without copying,
registering, normalizing or downloading files. An optional Writer metadata
browser uses bounded read-only Drive listings within the inbox/imported roots.
The automated-import page remains an optional administration route. See
`docs/writer-workflow.md` for exact
classification rules, storage references, limitations and rollback.
