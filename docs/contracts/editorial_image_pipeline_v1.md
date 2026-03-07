# Editorial Image Pipeline v1

## Scope
Contract for the editorial image flow in the common pipeline (`edition_steps`) for TXT and HTML lanes that reach `?allow_html_to_common=1`.

## Non-Negotiable Rule
- The image pipeline controls and actions are mandatory project surface area.
- They must not be removed, renamed, hidden, or behaviorally changed without explicit user order.

## Mandatory UI Controls
- `Salvar e converter imagens`
- `Upload ZIP images`
- `Consolidate internal images`
- `Insert page headlines`
- `Insert image placeholders`
- `Apply images to PRE_EDITION`

## Mandatory POST Actions
- `upload_images_files`
- `upload_images_zip`
- `consolidate_images`
- `insert_headlines`
- `insert_images`
- `apply_images`

## Canonical Paths
- Uploaded/converted source images:
  - `data/images/<book>/<lang>/*.jpg`
- PRE_EDITION source:
  - `data/builds/<book>/<lang>/BOOK.PRE_EDITION.md`
- Applied image assets:
  - `data/builds/<book>/<lang>/assets/images/*.jpg`

## Behavioral Invariants
- `upload_images_files` converts uploaded images to JPG and saves them into the active image directory.
- HTML common-lane POSTs must preserve `?allow_html_to_common=1` and return to `#transformacao-editorial`.
- `insert_images` injects placeholders into `BOOK.PRE_EDITION.md`.
- `apply_images` replaces placeholders with Markdown image refs and copies assets into `assets/images/`.
- Chapter images must remain positioned after the chapter heading and before the body text.

## Definition of Done
- The controls render in `edition_steps`.
- Multi-image upload persists JPG files in `data/images/<book>/<lang>/`.
- Placeholder insertion updates `BOOK.PRE_EDITION.md`.
- Apply images updates `BOOK.PRE_EDITION.md` and materializes files in `assets/images/`.
- Tests in `web/pipeline/tests.py` enforce this contract.
