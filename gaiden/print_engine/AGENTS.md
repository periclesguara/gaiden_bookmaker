# Print engine boundary

These instructions apply to `gaiden/print_engine/`.

- This package is the isolated final print-production stage.
- It consumes downstream approved editorial content; it never reads directly
  from Writer and never creates or rewrites prose.
- Do not import Django, `web.writer`, or `gaiden.writer_engine` here.
- Do not require a database, model endpoint, corpus, or Writer runtime.
- EPUB approval remains an upstream editorial gate. Print rendering must not
  mutate the approved EPUB or its source content.
- Generated TeX, PDF, manifests, proofs, and covers belong in external runtime
  storage, never in Git.
- Keep unit and executable smoke tests under top-level `tests/` and run them in
  the dedicated print CI workflow.
