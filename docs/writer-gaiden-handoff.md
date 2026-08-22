# Writer → GPT Plus Work → Gaiden Bookmaker

## Ownership

Writer ends when it has merged every finalized chapter into one body. It does
not reproduce Gaiden's manual pipeline, add frontmatter or images, build books,
publish, or call OpenAI cloud for translation.

Gaiden Bookmaker starts only after the human/GPT Plus Work pass returns a body
marked `GAIDEN_BODY_READY`.

## Outbound package

A staff-only POST in Writer creates, under `WRITER_HANDOFF_ROOT`:

- `body.md`: project title, chapter headings and exact finalized chapter text;
- `WRITER.HANDOFF.json`: versioned manifest with project identity, language,
  body byte count and SHA-256.

The action is idempotent for identical content and fails closed if a previous
package contains different bytes or an inconsistent manifest. Both files are
completed in a private staging directory and the package directory is then
published atomically. The configured root may be an rclone mount or another
directory synchronized with Google Drive. Ordinary tests never contact Drive
or a model.

Outbound status is `AWAITING_GPT_PLUS_WORK`.

## Return package

The GPT Plus Work stage works on a copy in Google Drive. The return package must
preserve `handoff_id`, replace the body hash and byte count with the final
values, and set:

```json
{
  "status": "GAIDEN_BODY_READY",
  "destination_system": "gaiden_bookmaker",
  "gaiden_entry_stage": "FRONTMATTER_ASSETS",
  "skip_stages": ["BLOCK_01"]
}
```

Gaiden must reject a missing/unknown contract version, non-matching SHA-256,
partial body, outbound-only status, path escape, symlink or unexpected file.
Import must be idempotent and must never overwrite the canonical original.

## Gaiden continuation

After a valid return:

1. bind the body to the canonical Work/Edition identity;
2. record the Writer handoff ID and final SHA-256;
3. skip Block 01 because the complete body already exists;
4. add frontmatter;
5. add images and cover assets;
6. build and validate EPUB/PDF.

The Gaiden-side importer is a separate behavior change and must not import
Writer models or `writer_engine`.
