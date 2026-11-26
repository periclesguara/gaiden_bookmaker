# Canonical EPUB chapter layout

An illustrated chapter retains its illustration and complete chapter text in
their authored order. The master premium theme uses
`illustration_opening_separate_xhtml: false`.

The EPUB spine contains `chapter_NNN.xhtml`, with the chapter heading, images
and complete prose. The renderer no longer extracts a figure into a separate
`chapter_NNN_opening.xhtml` document. Image anchors and the principal CSS remain
authoritative; document consolidation does not impose a fixed screen size or
shrink artwork to fit text beside it.

Preview navigation must use these XHTML documents, not raw image links.
Prepared text, frontmatter, and gallery images are preserved. This contract
belongs to EPUB rendering, not Intake or print/LaTeX production.

Run the database-free regression test with:

```bash
python web/manage.py test editorial.test_epub_reading_layout --noinput
```
