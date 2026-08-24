# Gaiden modular home, Writer, Intake, Manual / AI and Production boundaries

## Official home

`/` is the read-only Gaiden home. It exposes five independent cards with live,
read-only counters: Intake, Collections, Bookmaker — Manual / AI, Writer, and
Projetos Finalizados. Each card opens the owning module. Home and dashboard GET
requests are covered by side-effect tests.

## Ownership

| Context | Owns | Must not do |
|---|---|---|
| Writer | Manuscript editing, immutable versions, preview, export, explicit canonical promotion | Allocate book codes, call agents, write Intake Drive folders, build EPUBs |
| Intake | Deterministic discovery, hashing, staging, validation, normalization, headings and external handoffs | Import OpenAI/agent/prompt modules or execute Translate, Refine or Polish |
| Manual / AI | Explicit Translate, Refine, Polish and agent jobs | Start implicitly from Intake or Writer |
| Production Dashboard | Read projections and navigation; final download/details/history | Mutate state on GET or infer DONE from file existence |
| Projetos Finalizados | Read the latest valid final `EditionBuild` projection | Duplicate build state or run editorial production |

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
- `/pipeline/editions/imported/` lists canonical Drive items registered by the
  Intake. Its reading-preview GET downloads to temporary staging, verifies the
  registered SHA-256 and extracts bounded text without creating an edition,
  audit event or canonical artifact. Sending the selected file to the editing
  block remains an explicit CSRF-protected POST.
- `/manual/` owns selection of Translate, Refine and Polish. An edition is
  opened explicitly through `/manual/editions/<edition_id>/`; existing agent
  implementations and model choices are preserved.
- `/pipeline/editions/dashboard/` is a read-only projection on GET. Mutating
  final-artifact state uses CSRF-protected POST endpoints.
- `/finalized-projects/` is the canonical finalized-project projection. It has
  no persistence table and therefore cannot diverge from `EditionBuild`.

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
internal links and mandatory EPUBCheck. Missing EPUBCheck fails closed; only the
isolated unit-test settings may explicitly skip it. The validation audit stores
tool/version, timestamps, result, error/warning counts and output summary.

The importer copies to a temporary file, verifies size and SHA-256, validates
that temporary artifact, writes the locked database state, promotes through an
atomic rename and compensates the filesystem if the database transaction fails.
It refuses path escapes and filename/version collisions. `NO_OP` requires exact
edition, locale, version, SHA-256, size, source and a still-valid final build.

`sync_finalized_projects` reconciles any edition or `--book-code` explicitly.
It is idempotent, reports every unmet requirement, revalidates legacy final
records lacking EPUBCheck evidence and records a single synchronization event.
Marking a build OUTDATED requires confirmed POST plus reason, locks the build,
preserves its bytes/history and downgrades contradictory pipeline DONE state.

Final downloads require `qualifies_as_done`, canonical path containment, `.epub`
extension, registered byte size and streaming SHA-256 equality. Integrity
failures are denied and audited without loading the complete EPUB into memory.

Writer promotion requires an explicit `EditorialWorkLink`; textual code equality
is not an identity contract. Missing or inconsistent mappings fail closed before
canonical files or builds change. Successful promotion stores actor, reason,
previous/new paths and hashes and invalidates mapped final builds transactionally.

## CI and PostgreSQL

`.github/workflows/architecture-ci.yml` runs with PostgreSQL 16 and the pgvector
image, prepares the extension before migrations, installs pinned CI dependencies,
runs system/migration/compile checks and the Intake, Writer, Manual AI,
Collections, Dashboard, final import, architecture, security and Premium EPUB
suites. Local PostgreSQL users still need an administrator to install `vector`
in the disposable test database before the PostgreSQL suite can run; this
dependency must never be hidden by switching that suite to SQLite.

Approved external EPUBs remain canonical-storage artifacts and must never be
added to Git. Recovery scripts for individual books are not imported by runtime
code and are not part of this contract.
