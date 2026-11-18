# PrintBook proof quality

The editor measures saved enhanced artwork using the original pixel dimensions
recorded in the cover design. Interpolation does not change the original effective
DPI. Selecting a replacement image measures the new upload instead. Zoom and fit
continue to affect printed size and effective DPI.

UICLAP proofs below 60 pages omit the spine and remain drafts. The current barcode
reservation is an estimated layout position, not a provider template approval.
Final production still requires the provider geometry and adequate source images.

The original artwork, prior builds and interior approval are preserved. No schema
or database migration is needed. Reverting the editor change restores the former
display but also its misleading DPI reading for interpolated images.

Validation: `python web/manage.py test web.printbook.test_cover_editor_controls
--noinput` (21 tests), repository hygiene and tracked-secret scan pass locally.
This focused validation does not replace full CI; PR #41 remains draft with
unresolved integration checks.
