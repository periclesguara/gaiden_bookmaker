# Writer project references

The project panel and registration form use one **Corpus do Projeto** selector.
Canonical Intake Works are linked through `ProjectReference.intake_work`.
Selection never copies manuscripts or indexes and does not run normalization,
embedding, chapter generation, downloads or editorial build services.

Search by author or title, choose a subset, or select all works of an author
including paged works. Saving from the panel persists the links and returns to
the panel. Saving from the registration form stages the draft until the project
is saved. Additions use canonical Work identity; omitted links are retained.
Unchecking a previously selected work or clearing its author sends explicit
unlink IDs. Removal affects that project only. Reference roles and notes are
preserved and are not required by the selector.

Status comes from backend records and hash-verified external artifacts on every
request. The panel groups selected works by author and shows preparation,
vectorization and RAG readiness. Technical failures live under **Ver detalhes**.
The Reference Bible remains a separate textual guide for using the corpus.

RAW works can be prepared directly in Writer through the existing normalization
service, without an EPUB final build. Shared `SourceVectorIndex` records support
either a canonical Work/SourceDocument pair or their original EditionBuild link.
Valid existing build-owned indexes remain reusable. Pending vectorization never
duplicates READY vectors into a project-specific index.

See [Writer corpus and sequential chapters](writer-corpus-flow.md) for supported
formats, the sequential contract/summary workflow, migration 0018, context
limits, exact checks, deployment requirements and recovery.

Migration `writer.0017_ensure_language_contract_column` remains the prior forward
repair for a missing physical JSONB column. It preserves existing values and is
not rewritten by this change. Migration 0018 extends shared reference indexing;
it neither drops existing rows nor updates manuscripts or publication status.
Persistent deployment requires a verified backup and disposable-clone rehearsal.
