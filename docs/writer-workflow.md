# Writer deployment and file-treatment workflow

## Scope

This is the phase-2 operator workflow layered on the local Qwen/RAG engine.
It adds Django records and controls for source selection, normalization,
vectorization, Fiction story bibles, a Nonfiction chapter-development mode, a versioned
JSON language contract, chapter planning, generation sessions, source-validated citations,
and explicit editorial finalization.

It does not store model weights, source manuscripts, normalized bodies, vector
indexes, or generated files in Git. Every Writer route requires an authenticated
Django staff user; anonymous operators are redirected to the admin login.

## Deployment rule

Before deployment:

1. back up PostgreSQL and record the current application commit;
2. confirm that `GAIDEN_WRITER_SOURCE_ROOT` points to the read-only corpus
   directory and `GAIDEN_WRITER_STORAGE_ROOT` points to writable external
   storage;
3. verify ownership and permissions without making the source corpus writable
   to the web process;
4. verify the separate loopback Qwen generation and embedding services;
5. review `python web/manage.py migrate --plan`;
6. apply Writer migrations through
   `writer.0004_supporting_cast_revisions`;
7. run `python web/manage.py check` and the protected test suite;
8. open `/writer/`, but do not trigger paid or GPU work during deployment
   smoke tests.

The Writer migrations create the cast-revision table, add nullable/defaulted cast-audit
fields to chapter sessions, add the defaulted project writing mode, and add the optional per-chapter source-guidance field, and add the non-destructive
many-to-many selection of approved sources for each chapter. They do not alter editorial or pipeline
tables, delete operator data, or touch files. Existing sessions retain blank
cast-audit fields and are treated as legacy when a partial chapter is resumed.

Rollback:

1. stop new Writer actions;
2. preserve the database and external Writer directory;
3. redeploy the previous application commit;
4. leave the new tables in place until a reviewed forward decision is made;
5. derived indexes may be deleted and rebuilt, but canonical sources and
   finalized chapter records must not be deleted.

## Six-stage operator flow

### 1. Select and normalize files

The Files page discovers UTF-8 `.txt` and `.md` files below the configured
source root. The operator checks rows and sends them to Normalize.

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
the selected set through Qwen3-Embedding-0.6B.

All selected files must be represented. The index is an external, derived,
atomic artifact and can be rebuilt from normalized sources.

### 3. Select the writing mode and define the language contract

Each project selects **Fiction** or **Nonfiction** and has a mandatory, validated
JSON language contract. The mode becomes immutable after the first generation
session. Fiction bibles define people, facts and events; the contract defines how
every session must express them. Nonfiction does not require creative bibles.

The project form exposes one language selector inside the Writer flow:

- `EN-US`: contemporary American English creation;
- `EN-UK` (stored as `en-GB`): contemporary British English creation;
- `PT-BR`: Brazilian Portuguese translation and modernization.

The Writer loads the matching contract automatically; operators do not edit the
contract JSON in the form. The selected language and the contract's
`target_language` must match. The language becomes immutable after the first
generation session.

The contracts declare:

- British English source references and American English output;
- original-fiction generation from semantic reference content only;
- preservation of meaning, characters, plot facts, chronology, causal logic,
  point of view and dialogue intent;
- rejection of source wording, source style, Victorianism and British usage;
- strong archaicism reduction, obsolete-connector removal, repetition control
  and a maximum sentence-length target;
- terms to delete, terms to reject, and exact replacement mappings;
- no-summary, no-commentary and no-new-facts constraints;
- accepted word-count variation and zero to three bounded retries.

The versioned presets are:

- `docs/examples/language-contract.en-us-original.json`;
- `docs/examples/language-contract.en-gb-original.json`;
- `docs/examples/language-contract.pt-br-translation.json`.

Unknown keys, missing keys, duplicate terms, conflicting delete and replacement
rules, and invalid ranges block saving. The contract's `target_language` is
the authoritative output language.

Exact replacements and deletions are applied deterministically after Qwen
returns text. The resulting session is rejected when a forbidden term remains
or word count is outside the configured range. Fluency, meaning preservation
and archaicism reduction remain model/editorial judgements and therefore still
require human review.

Each project also has explicit fields for:

- character bible;
- antagonist bible;
- supporting characters bible;
- scenarios and locations;
- world, period, climate and references;
- premise;
- story direction;
- general story outline.

Generation is blocked while any required bible or direction field is empty.

The supporting cast uses one project-level field, not one field per character or
chapter. The **Generate supporting-characters bible with AI** action is POST-only
and asks Qwen for a JSON registry of 3–12 narratively necessary characters. Each
record has a stable `SUP-NNN` ID, canonical name, aliases, role, physical
markers, traits, voice, goal, relationships, knowledge limits, continuity rules,
and authorized chapters. The service rejects invalid chapter numbers and
duplicate IDs, names, or aliases before saving. Operators can edit the generated
field before chapter generation starts.

Every generation session receives a compact global identity map and the complete
records only for supporting characters authorized for that chapter. The prompt
forbids merging identities or transferring aliases, traits, relationships,
roles, goals, or knowledge. Legacy free-text supporting-character bibles remain
accepted and receive the same identity-separation instruction.

After the first cast exists, the project page exposes **Update Bible with AI +
RAG** and an operator textarea for a new character, reference, or continuity
gap. The update retrieves eight semantic reference chunks from the project's
approved vector index, sends them to Qwen as untrusted reference data, validates
the complete returned registry, and creates an immutable numbered
`SupportingCastRevision`. Updating requires a vectorized project.

Schema version 2 distinguishes an actual canonical identity from an original
character inspired by semantic traits. A canonical source records work,
chapter/story, and canonical character. A reference anchor records work,
chapter/story, reference character, traits used, and required differences.
References never authorize copying source wording or style.

Updates may add characters, aliases, traits, relationships, knowledge limits,
continuity rules, references, and future chapter appearances. They cannot
delete or rename an existing identity, remove an alias, or remove a previous
chapter appearance. New IDs remain sequential. The registry supports up to 24
characters while chapter prompts still include full records only for the
relevant chapter.

Each new chapter session stores the exact cast snapshot, SHA-256, and revision
used for generation. Existing sessions remain unchanged after a later cast
revision. Resuming a partially generated chapter with a different cast revision
is rejected; the operator must create a versioned chapter revision. Direct
editing of the cast field is blocked after the first session so in-progress
changes pass through the audited update tool.

### 4. Direct Fiction or develop Nonfiction

Every Fiction chapter has its own title, direction, and script. Project bibles
and the general outline provide global continuity. Completed earlier sessions
provide local continuity to the next session.

Every Nonfiction chapter has three required editorial inputs and one optional
query aid in addition to its title and generation parameters:

- **Direction**: thesis, objective, limits, and expected structure;
- **Operator text**: the prose, arguments, and notes Qwen must improve, expand,
  and organize without replacing the thesis;
- **Chapter sources**: one or more exact files selected from the project's
  approved and vectorized corpus;
- **Optional source guidance**: subjects, periods, or questions used to formulate
  the semantic query inside only those selected files.

Nonfiction generation is blocked if direction, operator text, or the exact
chapter-source selection is empty. Source guidance may remain empty. It does
not require character, antagonist, supporting-cast, scenario, world, story
direction, or story-outline bibles.

Retrieved corpus text is untrusted reference data. It cannot change system
rules or promote output. In Nonfiction, the engine first filters the project index to the files selected
for that chapter; Qwen may add a factual statement only from the retrieved
context within that subset. Every substantive factual paragraph must end with
one or more exact `[SRC:<chunk_id>]` markers. The engine rejects missing
markers and IDs outside that retrieval result, then replaces valid markers with
session-unique footnotes and appends source path, heading, and chunk ID. This
first contract provides verifiable provenance; normalized bibliographic
metadata can later enrich the displayed note without weakening the source-ID
validation.

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
content, actual word count, model, retrieval IDs, scores, parameters (including
writing mode and citation-contract version), the complete language contract and
SHA-256, plus the supporting-cast revision, snapshot, and SHA-256 when Fiction
uses them. Nonfiction stores empty cast audit fields and retains the exact
retrieval IDs behind every rendered footnote.

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
