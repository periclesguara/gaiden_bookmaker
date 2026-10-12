# Canonical corpus

## Boundaries

`web/corpus` adds tables; it does not replace Work, Edition, EditionText,
SourceDocument, Writer references, or legacy indexes. Existing projects receive
`LEGACY_WRITER` configuration without changes to their rows. New projects receive
`CANONICAL_CORPUS` with zero selected Works. The staff-only `/corpus/` catalog
and `/corpus/projects/<id>/` selection use the same Work-rooted service.

Raw sources and all outputs live below external `GAIDEN_STORAGE_ROOT`.
Materialization uses a named representation and SHA-256. Equal content within
one Work and representation is reused, retaining origin provenance. A different
canonical source is a review conflict, not an implicit replacement. Symlinks,
paths outside storage, and changed bytes fail closed. Legacy raw paths and
EditionText inline raw text remain valid resolver fallbacks; final EPUBs are
not preferred sources.

## Manifest And Normalization

The operator JSON manifest declares `batch_code`, `author_id`, `author_name`,
`work_codes`, and a `sources` array. Each source declares `work_code`,
`title`, `approved_existing_titles`, `provider`, `provider_file_id`,
`representation`, `raw_path`, `sha256`, and `provenance`. Provenance records the
public URL, remote version, materialization, `body_start_heading`, and reviewed
`section_titles`; `body_end_before` is optional. These boundaries must be
reviewed against the complete source before registration. The manifest is not
a Work. No fuzzy matching or automatic Work creation occurs.

Normalization removes recognized distribution markers and explicitly reviewed
technical boundaries. It does not translate, modernize, summarize, or rewrite
literary text. Narrative prefaces and section text are preserved. A missing
boundary, missing required story, residual Gutenberg boilerplate, or empty body
blocks the entire stage. This is not a general HTML/EPUB cleaner: unsupported
representations require a separately reviewed materialization first.

## Commands

Back up the database and dirty checkout before any persistent operation.
Rehearse on a restored disposable PostgreSQL 16 / pgvector clone, review
`python web/manage.py migrate corpus --plan`, then apply the additive migrations.
Never fake migrations or apply unrelated pending migrations as part of this
procedure. Configure a loopback Ollama URL via `GAIDEN_LOCAL_MODEL_URL` and the
installed model, verified manifest digest, and dimension via
`GAIDEN_CORPUS_EMBEDDING_MODEL`, `GAIDEN_CORPUS_EMBEDDING_DIGEST`, and
`GAIDEN_CORPUS_EMBEDDING_DIMENSION`. No remote or paid inference is used.

```sh
python web/manage.py corpus_batch preview --manifest /external/manifest.json
python web/manage.py corpus_batch register --manifest /external/manifest.json --confirm
python web/manage.py corpus_batch normalize --batch doyle_sherlock_complete
python web/manage.py corpus_batch chunk --batch doyle_sherlock_complete
python web/manage.py corpus_batch vectorize --batch doyle_sherlock_complete \
  --model nomic-embed-text:v1.5 --model-digest VERIFIED_INSTALLED_DIGEST --dimension 768
python web/manage.py corpus_batch status --batch doyle_sherlock_complete
```

Require nine matches, zero new Works, and zero identity conflicts in preview.
Each stage validates the whole batch and commits database state atomically.
Failure records `FAILED`; immutable partial files may remain for validated
reuse, but cannot make a partial batch ready. Re-running the stages reuses
matching hashes, strategy versions, model digests, and dimensions. Dense
`VECTOR_READY` is distinct from legacy lexical BM25 readiness.

Only full validation of sources, normalized bodies, chunks, vectors, counts,
index hashes, and fingerprints yields `RAG_READY`. Selection persists Work IDs;
retrieval loads only their compatible dense indexes. Author-scoped selection
preserves previously selected Works belonging to other authors. Existing
projects cannot silently switch backend through the selection page.

## Validation And Recovery

```sh
python web/manage.py check
python web/manage.py makemigrations --check --dry-run
python web/manage.py migrate --check
python web/manage.py test editorial pipeline writer corpus --noinput
bash scripts/ci/check_repo_hygiene.sh
python scripts/ci/scan_tracked_secrets.py
```

Tests mock embeddings and are offline. Real operator acceptance additionally
checks the nine-source batch and selected-only retrieval using the local model.
Compare protected legacy table hashes, project index paths, pipeline states,
and final-file hashes against the pre-operation backup.

For application rollback, remove the new routing/hooks by restoring the prior
source revision while retaining the additive tables and external files. Do not
unapply migrations after creating operator corpus records, delete legacy data,
or restore a whole database over newer operator activity automatically. Restore
from the verified pre-operation dump only under an explicit recovery plan.
The approved Work identity correction is independent of feature activation.

## Operator Checkout Integration

The approved-main implementation uses the generic WriterEngine. This operator
checkout has an additional editorial contract workflow: its source retrieval
and dashboard delegate to `corpus.writer_adapter` only for explicitly canonical
projects. The old selectors and preparation routes remain available for legacy
projects; canonical projects use the separate staff-only selection page.

The operator migration graph creates StoryProject in
`writer.0003_writer_workflow` (verified source provenance: the forward migration
recovers the operations from PR 18, commit 788a5842). Its new corpus initial
migration therefore depends on that node. Approved main creates StoryProject
in `writer.0001_initial` and retains that dependency in the isolated feature
branch. The initial operator dependency was corrected after the clean-schema
rehearsal exposed the distinction; its DDL was unchanged, the required Writer
dependency was already applied, and no historical Writer migration was edited.

This namespace-packaged Writer requires explicit test-module labels rather
than discovery of the `web` namespace:

```sh
python web/manage.py test editorial pipeline corpus web.writer.tests \
  web.writer.test_editorial_flow web.writer.test_google_docs \
  web.writer.test_validation web.writer.test_reference_system \
  web.writer.test_narrative_workflow web.writer.test_project_references \
  --noinput --keepdb
```
