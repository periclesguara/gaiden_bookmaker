# PrintBook proof quality

The editor measures saved enhanced artwork using the original pixel dimensions
recorded in the cover design. Interpolation does not change the original effective
DPI. Selecting a replacement image measures the new upload instead. Zoom and fit
continue to affect printed size and effective DPI.

Cover auto-fit keeps back-cover copy inside the green safety guide on all sides.
It repositions text blocks within their allowed ranges before reducing type size.
Applied changes remain in the editor until saved and trigger the unsaved-changes
warning. When the provider barcode is enabled, auto-fit leaves a separate vertical
clearance above the barcode reservation. Saving from the montage submits a draft,
shows progress, and returns to the montage tab when the save succeeds.

UICLAP covers for interiors below 60 pages use a zero-width spine.
Front cover, back cover and spine settings can be saved from their own tabs.
The assembled proof omits the spine. If generation fails, the editor returns
to the current tab with the error so the operator can revise and retry.

The stapled preflight accepts a composed proof or an imported one-page cover
PDF. It verifies the recorded interior and cover PDFs, creates a versioned
JSON report, and records editorial proof approval only if the local PDF and
artwork checks have no blockers. The page shows any blockers and offers the
report for download. The approval is bound to the current interior, cover
hash and report. A new cover, interior or preflight invalidates that evidence.

The proof status and production status are separate. A stapled UICLAP proof
can pass local quality control without a final production JPG or ISBN.
Export remains blocked until the production path is implemented and approved.
No schema migration or rewrite of existing cover files is needed.

Validation in an isolated database, with LuaLaTeX and Poppler available:

```bash
python web/manage.py test web.printbook.test_cover_editor_controls \
  web.printbook.test_preview web.printbook.test_uiclap_proof \
  web.printbook.tests.PrintBuildStateTests.test_stapled_configured_pdf_survives_guard_and_reaches_preflight \
  web.printbook.tests.PrintBuildStateTests.test_stapled_manual_montage_compiles_and_surfaces_preflight_blockers
```
