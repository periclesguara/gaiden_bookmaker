# Gaiden Print / Prepress Engine v1

## Goal

Generate a reproducible print edition downstream from the final editorial book
without coupling the book domain to a single print provider.

This is a final-production module. It consumes approved editorial content and
the already finalized EPUB; it does not discover sources, create prose, call
models, or import anything from `writer`, Django, or the operator database.
Writer ends when it hands an ordered, body-only set of finalized chapters to
the editorial pipeline. That handoff has no frontmatter, publishing metadata,
cover, EPUB, PDF, or print settings. The editorial stage owns those additions;
the print engine begins only after editorial approval and the final EPUB gate.

The core invariant is:

`EPUB_READY != PRINT_READY`

A print release is ready only after the finalized interior geometry, matching
cover geometry and preflight checks agree.

## v1 flow

1. Build an immutable `PrintSpec` (trim, binding, paper, typography, margins).
2. Render a no-bleed text-first interior as deterministic LuaLaTeX source.
   The renderer adds a dedicated final page with `The End` for English or
   `Fim` for Portuguese, then keeps the bound interior at an even final page
   count without placing anything after that final marker.
3. Compile locally with `latexmk`/`lualatex` when TeX Live is installed.
4. Record the final page count.
5. Obtain the spine width from the official provider cover template/calculator.
6. Finalize the `PrintSpec` with page count + spine width.
7. Derive the full-wrap cover geometry (back + spine + front + bleed).
8. Bind the cover to a geometry fingerprint. Any pagination/spine change
   invalidates the previous cover.
9. Produce a package manifest for `INTERIOR.pdf` + `COVER.pdf`.
10. Run preflight. A FAIL is a release NO-GO.

The manifest rejects a cover geometry produced from a different finalized
specification and accepts only local PDF filenames. This prevents a stale cover
or a path outside the package directory from being presented as part of the
current print package.

## Django operator module

`web/printbook/` is the database/UI adapter for the independent core. It is
available at `/printbook/` and accepts only an `EditionBuild` whose final EPUB,
EPUBCheck evidence, official body, approval metadata, sizes, and SHA-256 values
still satisfy `EditionBuild.qualifies_as_done`.

Creating a `PrintEdition` freezes the editorial build identifier, official body
path, and SHA-256. The source reference cannot be changed from the edition page.
Every compile, cover import, preflight, preview, download, and export verifies
the source and current artifact hashes. A mismatch invalidates the dependent
history and blocks release; it never rewrites the editorial source.

The MVP operator flow is:

1. Create a paperback edition for IngramSpark, selecting 5.5 × 8.5 or 6 × 9,
   white or cream paper, and a seeded typography profile.
2. Compile a new, versioned `InteriorBuild`. LuaLaTeX produces the PDF and
   Poppler supplies the authoritative page count.
3. Use the local CoverMaker to simulate the full wrap and crop the externally
   prepared front image. Front typography is disabled by default and appears
   only when the operator explicitly enables it. Edit the independent back-cover heading, quote,
   synopsis and author-bio blocks, inspect safe/bleed guides,
   and generate a versioned `DRAFT` proof. The browser reports the effective
   image resolution and the draft uses a clearly identified paper-caliper
   estimate only for visualization. Cover images and approved EPUB illustrations
   are prepared locally with OpenCV using deterministic print profiles. Processing
   preserves real pixel dimensions in the conservative profiles and records the
   engine version. The explicit production profile may upscale with Lanczos to
   300 ppi; the source remains archived and the scale factor is disclosed as a
   warning for mandatory visual inspection.
   `Save draft` versions the PDF, design JSON, original image and prepared image;
   reopening CoverMaker restores those controls and reuses the saved artwork.
   `Save final cover` requires the official template and produces a title-based
   PDF filename only when the cover has no blocking finding.
4. Obtain the official provider template for the final page count and replace
   the estimated spine width with the template value. A cover becomes `READY`
   only after that one-page template matches the generated full-wrap size.
   Imported complete covers and separate PDF panels follow the same gate.
5. Inspect the real PDFs as a page or two-page spread. Single-page navigation
   visits every page. Spreads place even pages on the left and odd pages on
   the right, with an empty slot before page 1 and after a final even page.
   Cover previews always show the single full-wrap PDF.
   The combined book preview places the current full-wrap cover beside entry
   points for the paginated interior and the IngramSpark preflight.
6. Run preflight. Local Poppler checks are supplemented by an administrator-
   provisioned industrial validator; missing evidence is a `BLOCKER`.
7. Export a local ZIP only after the current report contains no `ERROR` or
   `BLOCKER`. The package contains the PDFs, official template, generated TeX,
   JSON evidence, hash/version metadata, and first-page interior/cover previews.

Generated TeX, PDFs, reports, uploads, and ZIPs are placed under the configured
Gaiden storage root at `printbook/<edition-id>/<artifact-kind>/vNNNN/`; they are
not repository files. Failed and invalidated versions stay in the database and
storage for audit.

`PRINTBOOK_VALIDATOR` may point to an absolute, administrator-provisioned
executable. Gaiden invokes it without a shell and passes one generated JSON job
path. The executable must return JSON on stdout containing matching `hashes`, a
non-empty `tool_version`, `pdfx_standard`, and checks named `pdfx`, `color_icc`,
`effective_resolution`, `page_boxes`, `safe_areas`, `template_geometry`,
`barcode_isbn`, and `font_license`. Severity must be `INFO`, `WARNING`, `ERROR`,
or `BLOCKER`. A PDF/X success is accepted only for the standard named by the
active IngramSpark profile. Until such evidence is configured, PrintBook is
usable for composition and preview but intentionally cannot become
`PRINT_READY` or export.

The current Markdown adapter is text-first. It preserves headings and prose,
escapes TeX commands, and fails closed on images, HTML, tables, links, lists,
fenced code, or other rich structures instead of silently losing content. A
future structured editorial adapter must explicitly map and validate those
elements before this limitation can be removed.

## Migration and recovery

The PrintBook migrations are additive: they create only PrintBook tables and
seed the five initial typography profiles. They neither rewrite editorial rows
nor touch runtime artifacts. Before applying them to a persistent environment,
back up PostgreSQL and rehearse the exact migration plan against a disposable
clone. If application rollback is required after operators have created print
records, roll back the code while retaining the additive tables; do not run
`migrate printbook zero` unless those records and their external artifacts have
been separately preserved and deletion has been explicitly approved.

Validation commands:

```bash
python web/manage.py migrate printbook --plan
python web/manage.py migrate printbook
python web/manage.py makemigrations --check --dry-run
python web/manage.py check
python web/manage.py test printbook
pytest -q tests/test_print_engine.py tests/test_print_engine_cli.py \
  tests/test_printbook_manuscript.py tests/test_printbook_cover.py
```

## Standalone operation

Install the project and the host print toolchain:

```bash
python -m pip install --no-deps -e .
sudo apt-get install latexmk poppler-utils texlive-fonts-recommended \
  texlive-latex-extra texlive-luatex
```

Provide approved content as UTF-8 JSON. This transport contract deliberately
contains no Writer IDs or Django model references:

```json
{
  "title": "Approved Book",
  "author": "Approved Author",
  "language": "pt-BR",
  "chapters": [
    {"title": "Chapter 1", "body": "Approved final text."}
  ]
}
```

Build the final interior in external runtime storage:

```bash
python -m gaiden.print_engine build-interior \
  --input /external/approved/book.json \
  --output-dir /external/final/book_0001/print
```

The command emits `INTERIOR.tex`, `INTERIOR.pdf`, and the verified even page
count. After obtaining the matching spine width and final cover from the
provider template, run:

```bash
python -m gaiden.print_engine preflight \
  --interior /external/final/book_0001/print/INTERIOR.pdf \
  --cover /external/final/book_0001/print/COVER.pdf \
  --page-count 286 \
  --spine-width 0.600
```

Exit status is zero only when no blocking failure is detected. WARN/SKIP
results remain visible through `requires_review`; PDF/X proof is still a
manual release gate in v1.

## IngramSpark adapter

`gaiden.print_engine.providers.ingramspark.IngramSparkAdapter` models the public
file-preparation policy used by the first provider integration. It is not an
Ingram backend/API clone.

Production approval deliberately requires an explicit `spine_width_in` from
the official provider template/calculator. The CoverMaker estimate is stored
as `simulator_estimate`, remains `DRAFT`, and cannot satisfy preflight.

The adapter currently gates:

- provider identity;
- cover bleed and safe-area policy;
- interior safe margin;
- finalized page count;
- finalized spine width;
- no-bleed interior support for the first production path.

The finalized IngramSpark page count must be even. The spine width must be
calculated from that complete count, including any trailing blank page; an odd
count remains a NO-GO instead of relying on an implicit provider adjustment.

The PDF preflight can verify, when Poppler tools are installed:

- interior and cover exist;
- final interior page count;
- interior page dimensions match trim;
- cover is a one-page spread;
- cover dimensions match the calculated spread;
- detected fonts are embedded.

The standalone command reports PDF/X as a warning. The Django module adds the
fail-closed industrial-validator adapter described above; it never converts a
warning or missing tool into approval.

`PreflightReport.passed` means that the automated checks found no blocking
failure; it does not turn a skipped or warning check into evidence. Use
`fully_verified` to distinguish a report made entirely of PASS results and
`requires_review` to surface WARN/SKIP results. A missing Poppler executable is
reported as SKIP. If an installed `pdfinfo` or `pdffonts` cannot inspect a PDF,
the result is FAIL because a corrupt or unreadable artifact must never pass
preflight.

## Scope deliberately deferred

- bleed interiors with asymmetric outside bleed;
- provider-certified automatic spine-caliper calculation;
- free-form vector drawing; CoverMaker provides bounded image crop, typography,
  color, full-wrap, safe-area, bleed, barcode-zone, and 3D simulation controls;
- a bundled commercial PDF/X/prepress validator (the isolated adapter exists);
- rich Markdown/image composition in the Django text-first adapter;
- Lulu adapter;
- provider upload APIs.

These are adapters/stages around the stable core rather than reasons to delay
the first print engine.

### DONE catalog and Intake handoff

`/printbook/` lists only final `EditionBuild` rows whose status is exactly
`DONE` and whose approval, EPUBCheck evidence, EPUB and official-body hashes
remain current. No operator upload or in-progress Intake file can bypass this
boundary. Intake's completed-build dashboard and registered-file list expose
**PrintBook** only for eligible final sources.

The link opens `/printbook/source/<build-id>/`, where the operator can inspect
the final EPUB, choose page trim, paper and typography, create a physical edition
explicitly by POST, or reopen an existing one. GET requests never create a
PrintEdition or render new editorial artifacts. The source ID is bound to the
URL; submitted source overrides are ignored. The catalog is a live read-only
projection of editorial builds, not a second book import or a new database table.

The independent `printbook/source_preview.html` reads the approved EPUB's OPF
spine and serves bounded manifest resources from the ZIP without extraction.
Its sandboxed reader disables scripts, forms and external resources. Every page
and asset request rechecks DONE and integrity. It does not call the Intake
renderer, expose editorial approval actions or modify the EPUB. Printed page
geometry is inspected separately using the compiled interior PDF preview.

The physical-edition dashboard provides trim/typography configuration, interior
compilation, a local text-first cover builder, cover assembly/import, preflight
and export. The builder offers classic, modern and minimal layouts, title,
subtitle, author, back-cover copy, spine text, colors and an optional front
image. It runs LuaLaTeX with shell escape disabled and verifies a one-page
result. Panel PDFs, generated TeX, design JSON, official template and final
cover remain versioned in external storage. A failed assembly preserves prior
builds; successful assembly invalidates dependent preflight/export approvals
and still requires industrial preflight. Drag-and-drop crop controls and a
commercial template library remain outside this first local builder; a complete
externally designed wrap can also be imported.

This continuation adds no schema migration and applies no migration to an
operator database. Existing PrintBook migrations must be deployed using the
backup/rehearsal procedure above. Rollback consists of reverting the catalog,
Intake links and independent preview routes/templates; retain existing physical
editions and external artifacts.

The local base remains `65f6ea33` on
`feature/writer-continuity-contract-v1`, with pre-existing uncommitted Writer,
Intake and PrintBook work. The fetched default branch is `b04e3fad`. Integration
onto the approved base and CI on the integrated head remain pending. The earlier
broader SQLite suite ran 478 tests with 63 failures and 49 errors; those failures
have not been diagnosed as part of this focused continuation. The dependency
audit also reports findings for pinned Django and Pillow. No merge is recommended
until those validation blockers are resolved separately.
