# Writer deployment and file-treatment workflow

## Scope

This is the phase-2 operator workflow layered on the local Qwen/RAG engine.
It adds Django records and controls for source selection, normalization,
vectorization, story bibles, chapter planning, generation sessions, and explicit
editorial finalization.

It does not store model weights, source manuscripts, normalized bodies, vector
indexes, or generated files in Git. Writer routes open directly without Django
authentication. Generation and finalization remain POST-only, and Django CSRF
validation remains active for form submissions.

Because Writer has no application-level login, deploy it only on loopback or a
trusted private network with access control at the host or reverse proxy. Never
expose Writer directly to the public internet.

## Deployment rule

Before deployment:

1. back up PostgreSQL and record the current application commit;
2. confirm that `GAIDEN_WRITER_SOURCE_ROOT` points to the read-only corpus
   directory and `GAIDEN_WRITER_STORAGE_ROOT` points to writable external
   storage;
3. verify ownership and permissions without making the source corpus writable
   to the web process;
4. verify the loopback Qwen generation and embedding APIs (the verified local
   Ollama setup serves both on `127.0.0.1:8001` with separate model names);
5. review `python web/manage.py migrate --plan`;
6. apply the Writer migration shown by `migrate --plan`; on this stacked branch,
   the verified PR #18 operations are preserved in the forward migration
   `writer.0003_writer_workflow` after the existing manuscript migrations;
7. run `python web/manage.py check` and the protected test suite;
8. confirm that `/writer/` opens directly in an anonymous browser session, but
   do not trigger paid or GPU work during deployment smoke tests;
9. verify that the host firewall or reverse proxy prevents untrusted access.

The migration creates new Writer tables only. It does not alter editorial or
pipeline tables and does not touch files.

Rollback:

1. stop new Writer actions;
2. preserve the database and external Writer directory;
3. redeploy the previous application commit;
4. leave the new tables in place until a reviewed forward decision is made;
5. derived indexes may be deleted and rebuilt, but canonical sources and
   finalized chapter records must not be deleted.

## Six-stage operator flow

The project page is the operational dashboard for this flow. It validates the
selected-source counts, the on-disk index header, configured embedding model,
local model availability, required creative fields, chapter parameters, session
counts, and chapter status. Green stages are complete, the amber stage is the
next operator action, and gray stages are waiting on earlier prerequisites.
Stage and action buttons remain clickable in every state. Processing actions
open an in-page confirmation dialog before POST. A first run offers Confirm or
Cancel; a completed normalization or vectorization offers Remake or Cancel and
explains the impact. Remaking normalization invalidates related derived indexes;
remaking vectorization atomically rebuilds the derived index. Direct POSTs that
omit the required confirmation are rejected without processing. Pending actions
report their exact prerequisites.
The model availability probe runs only for a loopback OpenAI-compatible endpoint
and uses a short timeout; non-loopback services are not contacted by a page view.

### 1. Select and normalize files

The Files page first discovers `READY` canonical texts from Author Studio and
registers them by work code and title without copying or rewriting their files.
These canonical texts are immediately selectable as normalized RAG sources.
When `GAIDEN_WRITER_SOURCE_ROOT` is configured, the same action also discovers
additional UTF-8 `.txt` and `.md` files below that directory; the operator
checks those additional rows and sends them to Normalize.

Normalization is deterministic and records source SHA-256, normalized SHA-256,
provider, applied rules, character counts, output path, timestamp, and errors.
It can remove:

- Project Gutenberg start/end contracts and licenses;
- Internet Archive wrapper material;
- Standard Ebooks metadata, license and colophon material;
- YAML frontmatter and HTML tags in text exports;
- identified publisher backmatter that is not part of the narrative.

A narrative `PROLOGUE`, `PREFACE`, `INTRODUCTION`, chapter, part, book, or
`EPILOGUE` is body text. In particular, EPILOGUE is never a generic removal
marker. Unknown sources are not cut at arbitrary headings. If cleaning leaves
fewer than 500 characters, the operation fails for manual review.

Normalized files are content-addressed and written atomically. The source is
never overwritten.

### 2. Select and vectorize

A project selects only normalized files. Changing that selection invalidates
the previous index path. The Vectorize button creates a complete new index for
the selected set through Qwen3-Embedding-0.6B. The verified local Ollama tag is
`qwen3-embedding:0.6b`; chapter writing uses `qwen3.5:9b-q4_K_M`.

All selected files must be represented. The index is an external, derived,
atomic artifact and can be rebuilt from normalized sources.

### 3. Fill the creative bibles

Each project has explicit fields for:

- character bible;
- antagonist bible;
- scenarios and locations;
- world, period, climate and references;
- premise;
- story direction;
- general story outline.

Generation is blocked while any required bible or direction field is empty.

### 4. Direct and script the story

Every chapter has its own title, direction, and script. Project bibles and the
general outline provide global continuity. Completed earlier sessions provide
local continuity to the next session.

Retrieved corpus text is untrusted reference data. It cannot change system
rules or promote output.

### 5. Chapter parameter table

The project declares its chapter count, default 10. Saving creates missing rows
such as Chapter 01 through Chapter 10. Existing chapter rows are never deleted
when editing the project.

Each chapter row contains:

| Parameter | Rule |
|---|---|
| Target words | default 2,500; range 400–12,000 |
| Estimated tokens | displayed as approximately words × 1.45 |
| Sessions | integer from 1 to 4; default 4 |
| Minimum session size | 400 words |
| RAG results | 1–20; default 8 |
| Status | planned, generating, generation complete, failed, final |

For 2,500 words and four sessions, the working target is approximately 625
words per session. The model may vary naturally; actual word count is recorded.

### 6. Generate and finalize

The Generate chapter button is POST-only and CSRF-protected. It generates the
configured sessions in order and records immutable session rows containing
content, actual word count, model, retrieval IDs, scores, and parameters.

A retry resumes missing sessions and does not overwrite completed ones. After
all sessions, status becomes `GENERATION_COMPLETE`, not `FINAL`.

The editor reviews all sessions and uses a separate Finalize chapter button.
Finalization requires explicit confirmation, concatenates the configured
sessions, records the timestamp, and makes the chapter read-only in the
operator interface.

## Operational limitations

Generation currently runs synchronously in the web request. This is acceptable
for initial controlled operation but may exceed reverse-proxy timeouts. Before
multi-user or unattended production, move generation to a durable job queue
with idempotency keys, progress reporting, cancellation, and worker limits.

Do not expose local model endpoints publicly. Do not put complete manuscripts
or prompt contents in application logs.
