# Writer corpus and sequential chapters

The project detail page uses one **Corpus do Projeto** panel. Its author counts,
preparation counts, vector counts and RAG readiness are computed from enabled
`ProjectReference` links and persisted shared `SourceVectorIndex` records on
every request. The project registration fields and language rules are unchanged.

## Selection

**Selecionar obras** opens the same selector from the project panel or project
form. Search matches author names and work titles. Choosing an author shows
paged works; **Selecionar todas deste autor** includes unloaded pages (up to the
500-work selection limit). Selection displays titles and authors only.

Saving on the project panel POSTs to `writer:project_references` and redirects
immediately to that panel. Saving on the registration form stages the selection
until the whole project form is submitted. Additions are a union by canonical
`editorial.Work.pk`, not a replacement. Unchecking an existing work or clearing
an author sends explicit removal IDs; omitted links are retained. Roles, notes,
project bibles, manuscripts and shared indexes are not deleted by this operation.

## Preparation and shared indexes

`writer:prepare_corpus` coordinates the existing `normalize_document` service
only for enabled selected works that have no valid prepared body. It reuses an
existing hash-verified Intake body or an existing shared normalized document.
Otherwise it reads the registered Edition RAW path or IntakeItem canonical path.
TXT, Markdown, HTML and EPUB are supported through the existing text/EPUB
parsers. When a registered Intake canonical file is missing from local storage,
Writer downloads that exact `02_IMPORTED_RAW` object through the configured
rclone Drive adapter, stores it at the canonical path under `GAIDEN_STORAGE_ROOT`,
and verifies the recorded SHA-256 before normalizing it. Preparation performs no
model calls and does not change Intake production status, create an EPUB build
or alter the original file.
Unsupported formats and unavailable local files remain pending.

Migration `writer.0018_canonical_reference_preparation` extends the existing
`SourceVectorIndex`: optional canonical Work and normalized SourceDocument
links, and a nullable EditionBuild link. It adds no second index model and
preserves all build-owned index rows. The canonical Work link is unique.
Normalized bodies and indexes remain in configured external Writer storage.

**Vetorizar pendentes** now builds the shared reference index used by the Writer
RAG with deterministic local chunk metadata only. It does not call Ollama,
embedding models or external AI services. Prepared missing/outdated indexes are
rebuilt and valid READY indexes are reused, including older build-owned indexes
for the same Work. It can complete prepared works while other selected works
remain pending. RAG is ready only when every enabled selected reference has a
verified body and readable matching shared index. It never copies index rows
into a project-owned index.

The `/writer/sources/` page also lists projects with enabled corpus selections.
Each row exposes **Normalizar pendentes** and **Vetorizar pendentes** for that
project and returns to the same page after the POST, so operators can resolve
the corpus without leaving the source inventory. These project buttons do not
replace the legacy SourceDocument normalization table below them.

## Chapter contracts and continuity

Contract preparation preserves the existing local-package and manual Drive
writing workflow. It does not dispatch a model for chapter writing. Selected
references must all be ready. A chapter requires its direction and script;
every preceding chapter must be FINAL with final text and a persisted nonempty
continuity summary. The panel shows the first outstanding predecessor.

The immutable contract includes the Book Kit, effective Reference Bible, current chapter
plan, continuity ledger and factual summaries of **all** earlier chapters in
order. Chapter 1 has no earlier summaries. Earlier prose and the prose-based
narrative-memory index are excluded. Summaries and Reference Bible are mandatory
context: a budget overflow fails instead of silently dropping continuity.
If a project has not saved a custom Bible, the built-in global contract is used.
Conflict precedence is Bible, continuity, direction, outline, references, RAG,
then model knowledge.

RAG retrieval reads the selected shared indexes, ranks relevant passages locally
using BM25 and limits the result to eight passages, two per canonical work and
24,000 characters. Author directives in the Reference Bible get extra weight
when the named author's directive shares topic terms with the current chapter.
This is deterministic lexical guidance, not semantic interpretation of every
possible natural-language instruction. No embedding or generation call occurs
during contract retrieval.

Editorial **Finalizar capítulo** makes one local structured-summary request.
The factual record contains events/clues, characters, locations, established
facts, discoveries, unresolved threads, decisions and state changes. A valid
summary and ledger are saved before FINAL in one transaction. Transport or
schema failure preserves the imported chapter and leaves the next one blocked;
there is no automatic retry. Repeating a successful closure reuses its summary.
Already-final chapters missing summaries expose **Salvar resumo** on the project
panel; existing prose is preserved. Previously stored summaries are not rewritten
automatically. Operators must review model-generated facts for accuracy.

## Deployment and recovery

This work continues the locally approved Writer checkpoint
`4db4c89784deb85694403a697c63daff8e29792a` and its uncommitted reference/language
changes, on `fix/writer-corpus-editorial-flow`. It is not directly mergeable into
public `main` (`b04e3fad01717f3fd985c17e0c5bd84f424ae53d`): integrate and validate
the local dependency layer first. No existing PR is superseded or merged here.
The missing local SECURITY/governance documents were read from that main commit.

Before applying to a persistent database, make and verify an external pg_dump
backup, restore a disposable PostgreSQL 16/pgvector clone, review `migrate --plan`
and rehearse the pending migrations there. Then run in the configured environment:

```bash
.venv/bin/python web/manage.py check
.venv/bin/python web/manage.py makemigrations --check --dry-run
.venv/bin/python web/manage.py migrate --plan
.venv/bin/python web/manage.py migrate --noinput
.venv/bin/python web/manage.py migrate --check
.venv/bin/python web/manage.py test web.writer.test_editorial_flow web.writer.test_project_references web.writer.test_reference_system --noinput
```

Do not reverse migration 0018 after Work-owned index rows have been created:
the older NOT NULL source column cannot represent those rows. For an application
rollback, restore the previous code while retaining the additive schema and
operator artifacts; keep the verified backup for database recovery. Never delete
operator rows or index files merely to make a reverse migration succeed.
