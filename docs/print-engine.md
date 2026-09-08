# Gaiden Print / Prepress Engine v1

## Goal

Generate a reproducible print edition downstream from the final editorial book
without coupling the book domain to a single print provider.

The core invariant is:

`EPUB_READY != PRINT_READY`

A print release is ready only after the finalized interior geometry, matching
cover geometry and preflight checks agree.

## v1 flow

1. Build an immutable `PrintSpec` (trim, binding, paper, typography, margins).
2. Render a no-bleed text-first interior as deterministic LuaLaTeX source.
3. Compile locally with `latexmk`/`lualatex` when TeX Live is installed.
4. Record the final page count.
5. Obtain the spine width from the official provider cover template/calculator.
6. Finalize the `PrintSpec` with page count + spine width.
7. Derive the full-wrap cover geometry (back + spine + front + bleed).
8. Bind the cover to a geometry fingerprint. Any pagination/spine change
   invalidates the previous cover.
9. Produce a package manifest for `INTERIOR.pdf` + `COVER.pdf`.
10. Run preflight. A FAIL is a release NO-GO.

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

The PDF preflight can verify, when Poppler tools are installed:

- interior and cover exist;
- final interior page count;
- interior page dimensions match trim;
- cover is a one-page spread;
- cover dimensions match the calculated spread;
- detected fonts are embedded.

PDF/X conformance remains an explicit WARN in v1 until a dedicated machine
validator/conversion stage is added.

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
