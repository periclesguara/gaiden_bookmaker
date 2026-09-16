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
   The renderer appends a blank verso only when needed so the bound interior
   always has an even final page count.
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

v1 deliberately requires an explicit `spine_width_in` from the official
provider template/calculator. The engine does not guess paper-caliper formulas.

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

PDF/X conformance remains an explicit WARN in v1 until a dedicated machine
validator/conversion stage is added.

`PreflightReport.passed` means that the automated checks found no blocking
failure; it does not turn a skipped or warning check into evidence. Use
`fully_verified` to distinguish a report made entirely of PASS results and
`requires_review` to surface WARN/SKIP results. A missing Poppler executable is
reported as SKIP. If an installed `pdfinfo` or `pdffonts` cannot inspect a PDF,
the result is FAIL because a corrupt or unreadable artifact must never pass
preflight.

## Scope deliberately deferred

- bleed interiors with asymmetric outside bleed;
- provider-specific automatic spine-caliper calculation;
- cover artwork/composition UI;
- PDF/X conversion/validation automation;
- Django model/migration integration;
- Lulu adapter;
- provider upload APIs.

These are adapters/stages around the stable core rather than reasons to delay
the first print engine.
