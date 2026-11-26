# Canonical EPUB chapter layout

An illustrated chapter consists of an illustration page followed immediately
by the complete chapter text. The master premium theme uses
`illustration_opening_separate_xhtml: true`.

The EPUB spine places `chapter_NNN_opening.xhtml` immediately before
`chapter_NNN.xhtml`. The first document contains the illustration and chapter
heading; the second contains the prose. Do not combine these pages or shrink
the illustration to force prose onto the same screen.

Preview navigation must use these XHTML documents, not raw image links.
Prepared text, frontmatter, and gallery images are preserved. This contract
belongs to EPUB rendering, not Intake or print/LaTeX production.

Run the database-free regression test with:

```bash
python web/manage.py test editorial.test_epub_reading_layout --noinput
```
