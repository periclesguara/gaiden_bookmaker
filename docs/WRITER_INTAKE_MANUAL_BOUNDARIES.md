# Writer, Intake, Manual / AI and Production boundaries

## Ownership

| Context | Owns | Must not do |
|---|---|---|
| Writer | Manuscript editing, immutable versions, preview, export, explicit canonical promotion | Allocate book codes, call agents, write Intake Drive folders, build EPUBs |
| Intake | Deterministic discovery, hashing, staging, validation, normalization, headings and external handoffs | Import OpenAI/agent/prompt modules or execute Translate, Refine or Polish |
| Manual / AI | Explicit Translate, Refine, Polish and agent jobs | Start implicitly from Intake or Writer |
| Production Dashboard | Read projections and navigation; final download/details/history | Mutate state on GET or infer DONE from file existence |

The canonical `editorial.Work`, `editorial.Edition`, `EditionPipeline` and
`EditionBuild` remain the production identities. Writer deliberately layers on
the existing Author Studio catalog and does not introduce another `book_code`.

## Routes and handoffs

- `/writer/` owns manuscript navigation. Saving creates `ManuscriptVersion`
  only. A Writer promotion requires a preview followed by a confirmed POST,
  stores the previous canonical SHA/path, and marks mapped builds outdated.
- `/intake/` is the deterministic Intake entry. The established incremental
  import/Drive endpoints remain compatibility adapters under
  `/intake/automated/`; they contain no agent or OpenAI execution.
- `/manual/` owns selection of Translate, Refine and Polish. An edition is
  opened explicitly through `/manual/editions/<edition_id>/`; existing agent
  implementations and model choices are preserved.
- `/pipeline/editions/dashboard/` is a read-only projection on GET. Mutating
  final-artifact state uses CSRF-protected POST endpoints.

No module imports another module's views, forms or templates. Shared behavior
belongs in `gaiden.application`, `gaiden.domain`, `gaiden.infrastructure`, or
canonical editorial models. The architecture test parses every Python import in
`web/intake` and rejects OpenAI, agent, prompt, translate, refine and polish
dependencies.

## Final build contract

`EditionBuild` exposes independent `BuildStatus` values: `NOT_STARTED`,
`IN_PROGRESS`, `VALIDATING`, `READY_FOR_APPROVAL`, `DONE`, `FAILED`, and
`OUTDATED`. A row qualifies for the Dashboard's DONE projection only when the
latest build has all of the following:

- a final EPUB path, stored SHA-256 and byte size;
- structural validation success;
- explicit final and approval markers;
- locale and artifact source;
- an existing official body path and its SHA-256;
- validation, approval and completion timestamps.

The generic `import_final_epub` management command validates the requested
edition and locale, ZIP structure, EPUB mimetype ordering/compression, XML/XHTML,
internal links and EPUBCheck (when installed). It copies through a temporary file
and atomic rename, verifies the destination SHA, preserves prior builds, records
audit events and refuses filename collisions with different bytes. Repeating an
edition/SHA pair returns `NO_OP`.

Approved external EPUBs remain canonical-storage artifacts and must never be
added to Git. Recovery scripts for individual books are not imported by runtime
code and are not part of this contract.
