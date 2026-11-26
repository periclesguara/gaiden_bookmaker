# EPUB image placement and Intake preservation — 2026-11-26

## Scope and base

Operational base: `feature/author-first-library`, HEAD `9e7fffac`, with
pre-existing operator changes. The operator explicitly authorized the runtime
correction after initially requesting main-only work. This is not a merge of
the old branch stack into main. No migration, reimport, Drive write, or change
to the finalized Book_0008 is part of this correction. The subsequent explicit
request to commit and publish is recorded separately below.

The approved final EPUB remains build 128, edition 90, status `DONE`, with
`EPUBCHECK_PASSED`. Its SHA-256 remains
`a6b5a918d4003bfb54446790bb57195d33e45ea72b04a73e6d41edf1b2ecffb9`.
Its 15 chapter illustrations were inspected read-only through the new PrintBook
asset reader. Their authored positions, not numerical filename order, determine
the mapping. No book-specific mapping is encoded in application code.

## Behavior

- Existing Markdown image anchors and image placeholders are preserved on
  repeated insertion. Applying placeholders preserves all bytes outside those
  anchors, including frontmatter and source/Drive metadata.
- Automatic association requires explicit chapter and slot numbers. `02.jpg`
  means chapter 2, slot 1; `ch02_03.png` means chapter 2, slot 3. Upload order
  does not determine placement. Duplicate or unnumbered associations require
  review instead of silent renumbering. Missing slots never consume another
  chapter's image; unresolved placeholders block EPUB rendering.
- ZIP imports validate numbering, image decoding and size limits in staging
  before replacing the gallery. Invalid or ambiguous ZIPs preserve the previous
  gallery. A successful ZIP still replaces that gallery, as before.
- Upload cache invalidation removes only the generated mapping manifest, not
  images already referenced by text. Consolidation preserves other embedded
  image files instead of deleting the entire assets directory.
- Existing image positions win over gallery inference. Already embedded image
  content is not reattached to a different chapter by filename. Chapter maps
  do not insert another opening illustration into already illustrated text.
- A chapter keeps its illustration and complete text in one XHTML document.
  The legacy separate-chapter-opening flag no longer splits the chapter into
  an extra document. Explicit edition-owned body-opening artwork is not
  silently deleted. No principal CSS or typography was edited in this change.
- PrintBook reads approved images from the OPF reading order and chapter
  content, including inline images and legacy opening documents. It imports
  no EPUB CSS or layout settings and writes no upstream EPUB content.

## Constraints

Numbering identifies chapter/slot; it does not infer the narrative subject of
an illustration. Authored anchors remain authoritative. An intentional change
to an already positioned image requires editorial review, not a placeholder
reset. Out-of-range gallery chapters remain visible as renderer warnings.

PrintBook's existing manuscript interface supports one illustration per
chapter. Multiple chapter illustrations now cause an explicit review error
instead of silently dropping images; configuring multi-image print layouts
remains separate work. Only the existing supported raster formats are copied.

## Operational validation environment

Regression failures were recorded before the changes for image relocation,
wrong-slot fallback, duplicate numbering, discarded upload slots, extra XHTML
pages, deletion of embedded assets and ZIP collisions. Tests use synthetic
content, not manuscripts or Drive credentials. Shared filesystem state in the
premium-renderer tests was isolated per test; image contract tests now use
their temporary storage root rather than the operator's repository data.

Integration uses a disposable `pgvector/pgvector:pg16` container, not SQLite
and not the operator database. Clean migration, `check`, `migrate --check` and
`makemigrations --check --dry-run` passed. No production migration was run.
After very slow table-flush operations, `fsync` was disabled only in that
disposable test container. These tests validate application/schema behavior,
not crash durability; the production PostgreSQL configuration was untouched.
Python compilation, shell syntax, `git diff --check`, repository hygiene and
tracked-secret scanning passed. Dependency audit reports 119 vulnerabilities
across 3 pinned packages; dependency upgrades are outside this behavior fix.

Executed commands (with disposable PostgreSQL credentials and temporary storage):

```sh
python web/manage.py test pipeline.test_epub_image_integrity editorial.test_epub_reading_layout printbook.test_workflow --noinput
python web/manage.py test pipeline.test_epub_image_integrity editorial.test_premium_epub pipeline.tests.EditorialImagePipelineContractTests --noinput
python web/manage.py test pipeline.test_epub_image_integrity editorial.test_epub_reading_layout printbook.test_workflow printbook.test_preview --noinput
python web/manage.py test editorial pipeline web.writer.tests printbook --noinput --verbosity 2
```

- Focused placement/layout/PrintBook composition regressions: **31 passed**.
- Premium renderer and image integration contracts: **81 executed, 79 passed,
  2 failed**. The two failures concern publisher metadata (`MantaQuest` versus
  `Rinobooks`) and a nonbreaking space in Gutenberg source credit. Both were
  reproduced using the pre-change renderer without changing the working tree.
  The real EPUBCheck test passed. Neither metadata rule was changed to hide
  these pre-existing failures.
- Including the PrintBook preview forms: **51 executed, 49 passed, 1 failure
  and 1 error**. The remaining cases concern front-cover form validation and a
  request fixture without Django message middleware, outside image extraction.
- Final protected suite: **709 executed, 581 passed, 61 failures and 67
  errors**, without excluding failing modules. The new image regressions and
  updated reading-layout contract pass within that full run. Other failures
  include legacy storage expectations, Writer, import/build contracts and
  cover forms. Their entire pre-change baseline was not established, so they
  must not all be described as pre-existing. These are unresolved validation
  blockers, not covered by the focused success claim. The disposable database
  container was stopped and removed after validation.

Django was restarted with its existing PostgreSQL/storage environment. HTTP
checks for `/`, `/intake/automated/` and `/finalized-projects/` returned 200.
Downloading build 128 through the live application yielded the same approved
SHA-256 recorded above. No production export or import was triggered.

Local evidence logs are under `/tmp/gaiden-images-*.log`; they are not versioned
artifacts. This operational tree is not certified for merge: remote CI has not
run and broader application failures remain.

## Publication scope

GitHub was fetched and inspected before preparing the commit:

- `origin/main`: `b04e3fad01717f3fd985c17e0c5bd84f424ae53d`.
- `origin/feature/author-first-library`: `4b7ce110f5a11fcca344996b3a3cae400cb79793`.
- Approved local parent: `9e7fffacf5476216e35b5a00dabbed945fc1568a`, one
  unpublished layout-regression commit ahead of the operational remote branch.

The correction was extracted into an isolated detached worktree at that parent,
without switching or cleaning the running application. The 14-file publication
contains image placement, gallery safety, EPUB/PrintBook asset reading, focused
regressions and documentation only. The canonical storage-path helper and
non-destructive mapping invalidation are included as direct dependencies.
Unrelated Writer/Corpus work, print composition changes, CSS changes, publisher
metadata changes, migration files and generated artifacts remain local.

The branch is an existing operational stack, not a main-ready integration.
PR #44 remains a distinct main-based draft. Existing PrintBook drafts #41 and
#42 are not retargeted or merged. No new all-stack PR is created by this push.
The default branch is unchanged; publication is a normal fast-forward push to
the existing operational branch, not approval to merge.

The isolated publication snapshot is validated separately from the 709-test
dirty operational tree described above. Its test inventory does not include
the unrelated, still-unpublished test modules. Clean PostgreSQL migration,
system checks and migration-drift checks passed for this isolated snapshot.

Publication-snapshot results:

- **40 focused tests passed**: image integrity, reading layout, image pipeline
  HTTP contracts and independent PrintBook composition.
- **612 protected tests executed: 494 passed, 58 failures and 60 errors**.
  The focused tests also pass within this unrestricted run. EPUBCheck passed.
  The full suite remains a merge blocker; its failures were not removed or
  excluded to publish this correction.
- Python compilation, shell syntax, whitespace checks, repository hygiene and
  tracked-secret scanning passed with all 14 intended files staged.
- The dependency audit still reports 119 findings across 3 pinned packages.
  No dependency versions or workflow gates were weakened.

Evidence for the isolated snapshot is in `/tmp/gaiden-publish-*.log` on the
operator machine. Publishing the commit does not claim remote CI approval.

## Recovery

Pre-change copies of edited files are in `/tmp/gaiden-epub-placement-before`;
the original dirty-tree diff is `/tmp/gaiden-images-preexisting.patch`.
The focused incremental patch is `/tmp/gaiden-epub-image-placement-only.patch`.
Verify reverse applicability before
rolling back. Do not restore all of Git HEAD: it would discard unrelated
operator changes. Restart Django after a code rollback. No finalized EPUB,
database record, CSS, Drive record or persistent migration needs rollback.
