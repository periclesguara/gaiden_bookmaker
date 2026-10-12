# Writer workflow

The Writer uses only the loopback local model runtime. Configure
`GAIDEN_LOCAL_MODEL_URL` (default `http://127.0.0.1:11434`),
`GAIDEN_LOCAL_MODEL`, and `GAIDEN_LOCAL_EMBEDDING_MODEL`. The service refuses
remote endpoints and rejects Qwen, Phi, GPT, and OpenAI model names. No OpenAI
SDK or external model API is part of the Writer runtime.

Writer retains its existing source selection, normalization, retrieval,
chapter drafting and review. Manuscript finalization uses the existing
`ManuscriptRevision`, canonical pointer and project closure fields; it adds no
model, schema or migration.

## External production export

The Writer owns the external production contract and the return destination.
Each `WriterExport` belongs to one explicitly selected chapter. Chapters may be
exported and returned out of order; identity is validated from project, chapter,
export and version IDs rather than filenames, titles or list position.

```text
Gaiden_Writer/
└── Projects/
    └── <project>/
        ├── exports/
        │   └── chapter_<number>_<export_id>/
        │       ├── export_contract.json
        │       └── chapter_<number>_request.md
        └── return/chapter_<number>/<export_id>/
            ├── chapter_<number>_response.json
            └── chapter_<number>_response.md
```

If `drive_project_folder_id` is already configured, it is reused as the project
root. Otherwise Writer creates `Gaiden_Writer/Projects/<project>` in the
configured Drive account and persists that folder ID on the project. Set
`GAIDEN_WRITER_DRIVE_ROOT_ID` to place `Gaiden_Writer` under an operator-owned
root folder.

The database manifest stores the real export folder ID, return folder ID,
contract file ID, URLs and the single expected chapter filename. Expected
return files use the stable `chapter_01_response.md` convention. The chapter
contract contains the global rules, reference bible, chapter direction and
return folder URL. Writer does not call an LLM during export.

Import uses the persisted export and `return_folder_id`. The Markdown file must
have the exact expected name inside that export's return folder. If a response
JSON is present, Writer validates `project_id`, `chapter_id`, `export_id`,
`export_version`, status, content filename and any declared SHA-256. If JSON is
absent, Writer derives identity from the linked export/folder and expected
filename, checks any Drive `appProperties` identity fields, and computes SHA-256
from the downloaded UTF-8 bytes. Conflicting metadata, duplicate filenames,
extra chapter responses and incomplete folder listings fail closed. Imported
content, identity source and hash remain recorded in the export manifest;
duplicate content is skipped, and an approved chapter is never overwritten.
Import is technical receipt,
not editorial approval or finalization; `SAVE MERGE` and `SAVE FINAL` remain the
approval path.

The canonical operator path is **Project > Chapters > Exportar IA externa** (or
the same action on the chapter detail page), followed by **Verificar retorno**
and **Importar retorno** on the export record. Both pages display the same
per-chapter transfer panel: export ID/version, request, return folder, expected
response filename, imported Drive file ID, import time, word count, SHA-256,
error and next editorial step. The old `Chapter.drive_*` contract remains
read-only for historical access; its legacy export/import forms are not part of
the active workflow. The corresponding legacy HTTP routes remain for
compatibility and require a separate retirement review.

Every return must contain the exact `.md` name shown in the request file in
that export's `return_folder_id`. The `.json` file is optional; when supplied,
it must be in the same folder with the exact expected name. Producers should include
`project_id`, `chapter_id`, `export_id`, `export_version`, `contract_version`,
`status: "COMPLETED"`, `content_file`, `word_count`, and `sha256`. The request
file includes a filled identity template for these fields; the external writer
replaces the word-count and SHA-256 placeholders with values from the final
UTF-8 Markdown bytes. A click on **Importar retorno** downloads the Markdown
into the Writer; it does not require a separate upload form or background job.
Do not infer a chapter from the filename alone. A missing or mismatched return
errors without changing approved text. The folder-derived path relies on the
operator placing the correct prose in that export's unique return folder; use
the JSON when the external producer can provide stronger independent identity.
After `IMPORTED`, review and finalize that chapter, then save its continuity
summary before treating the next chapter as editorially released.

## Manuscript finalization

When all chapters are final, the project offers:

1. **SAVE MERGE**: reuse the existing ordered chapter merge service. Identical,
   intact content reuses its revision; different content creates a new immutable
   revision. Closed projects, damaged saved revisions and occupied paths are
   rejected rather than overwritten.
2. **EXPORT MERGE**: download the latest saved merge and preserve a byte-identical
   copy under `<writer-root>/projects/<id>/return/`, for example
   `book_7_merged_v001_export_v001.txt`. Each export has its own version and
   JSON record with revision, timestamp and SHA-256. Export never promotes a merge
   over a saved Final Version and remains available on closed projects.
3. **IMPORT FINAL VERSION**: **BROWSE COMPUTER** previews the selected filename
   before confirmation and reuses the existing TXT/Markdown/DOCX importer.
   TXT/Markdown must be nonempty UTF-8, without NUL bytes, up to 50 MiB.
   DOCX uses the existing paragraph extractor; its ZIP is bounded to 5,000
   entries and 100 MiB decompressed. DOCX formatting is not preserved.
   **GOOGLE DRIVE** lists a bounded page in the configured project folder, or
   accepts a file ID/Drive/Docs link. It reads TXT, Markdown or native Google Docs
   (plain-text export), using the existing Writer credentials and Drive client.
   It never updates the remote file. Version drift during download blocks the
   import. Repeating the same file/version/content reuses the local revision.
   `<revision>.source.json` records the file ID, name, remote version/date,
   imported date, download hash and local revision hash. This does not select the
   canonical source yet.
4. **SAVE FINAL**: explicitly select an imported revision as the canonical Final
   Version. It keeps that existing immutable file; it does not duplicate it in a
   second directory. The history permits selecting another imported revision
   before project closure. Missing selection, foreign-project revisions and
   damaged files are rejected.
5. **GO TO EDITION MODE**: require the operator's author and publisher credits,
   freeze the selected manuscript and close Writer for further editing, then
   reuse the existing Intake bridge and open **FRONTMATTER** directly. The route
   carries `edition_source=writer`; actual Writer origin is checked from stored
   provenance, not trusted from the query string.

The canonical priority is explicit: **saved Final Version → latest saved Merge
→ final chapters only when neither exists**. An imported but unsaved candidate
does not replace the source. Saving a newer merge never changes a saved Final
Version. A missing/damaged canonical file blocks entry; it never triggers a
silent reconstruction from chapters. The merge fallback displays
`No imported Final Version. The latest Merge will be used.`

Buttons reflect their prerequisites: export requires a saved merge; SAVE FINAL
requires an imported revision; entering Edition requires an available canonical
source, saved merge or complete final chapters. Upload selection must be confirmed
before it can be saved as Final.

Closure is persisted before the independently retryable Intake handoff. If
registration fails (for example, a missing configured seal), fix the stated
condition and repeat **GO TO EDITION MODE**. The closed manuscript remains
frozen; the retry reuses that revision and existing Intake edition. Do not remove
files or reopen chapters merely to retry registration.

## After Writer finalization

The Intake asset lane is the only downstream workflow for a Writer canonical.
It uses the existing Intake bibliographic template and follows **Frontmatter →
Figures → Assembly → Preview → Approve → Generate EPUB**. Figures reuses the
existing cover/gallery uploads and displays thumbnails. Assembly calls the
existing renderer with the hash-verified frozen manuscript, frontmatter,
figures, styles and TOC. It does not call the legacy text-transforming merge.

Preview navigation and **REGENERATE PREVIEW** reuse the shared EPUB template.
**APPROVE FINAL EDITION** requires an official cover and records the existing
approved fingerprint plus approval date and edition version ID in render state.
**GENERATE EPUB** packages through the existing renderer and final EPUB import
service; source/asset drift requires renewed approval. EPUBCheck must pass before
the registered final download is enabled. The asset lane reports validation
state and fatal/error/warning counts. Neither preview nor packaging rewrites the
Writer manuscript.

Writer canonical manuscripts must not pass through normalization, heading
cleanup, chapter splitting, translation, export, or import after finalization.
The Intake production dashboard and its transformation endpoints redirect or
reject requests for Writer-canonical editions. Do not reintroduce a Writer
route into those stages.

## Runtime data

Manuscripts, indexes, source corpora, generated EPUB/PDF files, images, and
covers remain outside Git. Store local model weights and operator data in the
configured external storage. Never put credentials or private source text in
configuration examples, logs, or commits.

## Feature base, validation and recovery

`feature/writer-finalization` starts from the explicitly approved checkpoint
`dc42ae259ce6f2b9a5f210a94f348faa5b391251` on
`recovery/printbook-20261001T194447Z`. This task intentionally does not reconcile
main, inspect PostgreSQL, change models or create/rewrite migrations. It depends
on the local revision/closure and Writer-to-Intake infrastructure included in
that checkpoint. Earlier recommendations to block all feature work on main
reconciliation are superseded for this task only.

Run offline feature tests using the existing disposable in-memory SQLite test
settings; the placeholder PostgreSQL variables only satisfy settings loading:

```bash
DJANGO_SECRET_KEY=unused PGHOST=unused PGDATABASE=unused PGUSER=unused \
  .venv/bin/python web/manage.py test \
  web.writer.tests.MergeSafetyTests web.writer.tests.FinalizationTests \
  web.writer.tests.DriveFinalizationTests web.writer.tests.EditionWorkflowTests \
  --settings=gaiden_portal.settings_test
```

These classes are in the protected `web.writer.tests` module so existing CI
discovers them. Drive is mocked; no real credential or remote manuscript is used.
SQLite validation is not PostgreSQL integration validation. The historical
migration reconciliation and PostgreSQL quality gates remain deferred by the
explicit scope exception, not claimed as passing.

The protected editorial/pipeline/Writer/PrintBook suite and dependency audit
must also be reported even when the approved checkpoint already fails them.
Do not weaken assertions or change unrelated dependencies to hide those failures.

Recovery uses the checkpoint branch and the externally verified snapshot at
`~/.local/state/gaiden-recovery/printbook-20261001T194447Z/`. Revert feature code
through a reviewed commit if necessary; keep all immutable manuscripts,
return exports, source metadata and existing Intake editions. No database rollback
or migration is required for this feature. The checkpoint intentionally preserves
preexisting local backup files; this feature does not clean them up.

Validation on 2026-10-01: 43 focused tests passed (37 new finalization tests and
six existing manuscript/Intake-bridge tests), using in-memory SQLite. Three
merge safety regressions were reproduced before their fixes. Django system
checks, changed Python syntax, repository hygiene and current-tree secret scans
passed. The final protected run executed 541 tests, compared with 504 in the
approved checkpoint: both retain the same 64 failures and 47 errors, with no
additional failing test name. All 37 new tests pass in that full run.
The dependency audit of unchanged `requirements-ci.txt` reported 73 findings
in the existing Django/Pillow pins. These inherited failures and the deferred
PostgreSQL checks prevent a merge recommendation. Drive and the EPUB registration
gate were mocked in focused tests; no live Drive transfer or production
EPUBCheck run is claimed.

# Reference system

Writer projects may define an optional **Reference Bible** and a **Reference
Corpus** shared by fiction and non-fiction. Corpus selection creates
`ProjectReference` rows pointing to final `DONE` `EditionBuild` records from
Intake/editorial storage; it never copies the canonical body or original file.

`SourceVectorIndex` is global per canonical source build. Its `source_hash`
must equal `official_body_sha256`; a mismatch changes a READY index to
OUTDATED and retrieval skips it. Selection never starts vectorization. The
operator explicitly uses Vectorize or Re-vectorize, and failures remain FAILED
with diagnostics while both the source and project relation are preserved.

Retrieval constructs its whitelist server-side from enabled project references,
optionally filters their extensible role lists, and returns source ID, title,
author, build version and deterministic chunk ID with every hit. Projects with
no selected READY references skip reference retrieval without blocking chapter
generation. Reference material is delimited and remains below project rules,
story direction and project bibles in the instruction hierarchy.

## Existing Intake titles and Writer folders

`/intake/` renders the imported-title catalog directly. It reads `IntakeItem`
records with an existing canonical path and existing editorial `Edition`/`Work`
relationships,
groups files and editions under each Work, and preserves stored titles and IDs.
Catalog-only Works without a registered source or edition are not listed.
Existing editions open through their production route; source-only titles link
to the existing registered-source table. No source is activated on page load.
The automated folder/import administration remains at `/intake/automated/` as
an optional operation rather than an obligatory landing page.

`/writer/library/`, linked from Writer Projects and Sources, exposes every
previously imported item with a canonical path, grouped by its existing
`IntakeBatch`.
A batch with a retryable failure still exposes its successfully imported
items. A later conflicting attempt does not hide a previous canonical path;
the current item status remains visible. Every format is listed, including PDF
and image references. Existing
`SourceDocument` paths and their project links, and historical
`Edition.raw_source_path` records, are exposed without registration or mutation.
Local files
under `GAIDEN_WRITER_SOURCE_ROOT` are also listed recursively, preserving their
relative folders and physical paths. Symlink files and directories are skipped.
An unavailable local root is reported without hiding the registered batches.

The library is a read-only reference projection, not a normalization, RAG
registration or extraction operation. Remote paths are those recorded at import;
loading the page neither enumerates Drive nor verifies remote availability nor
fetches unregistered remote files. The optional **Consultar pasta** browser calls
`/writer/library/browse/` only on explicit operator selection. It lists immediate
files and subfolders in the configured inbox/imported roots using bounded
`rclone lsjson` metadata calls; subfolders can be opened and every file format is
retained. Drive file IDs link to the existing file in Drive. It never downloads
content, imports a batch, or writes records. Paths outside those roots and
traversal paths are rejected; unavailable listings are reported without retries.
Local enumeration does not create
`SourceDocument` rows. Existing generation sources and project references keep
their original registration and processing flow. In particular, exposing a PDF
reference does not add PDF support to the TXT/Markdown generation pipeline.

PDF sources stay outside the Intake title catalog. Articles/papers/references
are excluded when `IntakeItem.metadata.kind`, `.role`, or `.document_type` is
`reference`, `article`, `paper`, `artigo`, `referencia`, `chapter`, `excerpt`,
`notes`, `bibliography`, or `project_auxiliary`, or when Work provenance
has that `document_type`. Existing unclassified TXT/EPUB sources are not guessed
from filenames or prose; they retain their catalog identity. This change does
not write classifications into operator records.

This repair has no migration, persistent write, transfer, file copy, rename,
content transformation, or schema repair in its GET routes. Restore the prior
navigation by reverting the focused repair commit; operator data needs no
restoration because it was never changed. The approved base is local
`feature/writer-finalization`, checkpoint `398618e3`, not the divergent main.

Targeted verification:

```bash
python web/manage.py test pipeline.test_intake_library
```

Run against a disposable PostgreSQL 16 database with pgvector. Never use an
operator database for a clean-migration rehearsal. The tests include ORM fixture
writes, reference exclusion, mixed file formats, partial batch completion,
unavailable directories and unchanged file bytes/mtime after browsing.

## Project reference bible

Each project uses the existing optional `StoryProject.reference_bible` TextField
(migration `writer.0013_reference_system`). No new schema or migration is needed.
The project dashboard has a collapsible **Bíblia de Referências** section with a
large plain-text textarea and **Salvar Bíblia** button. When the database field is
empty, the form starts from the built-in `REFERENCE BIBLE - GLOBAL CONTRACT`
template covering characters, world, setting, voice/language, genre rules,
reference authors/works, continuity, timeline, canon, forbidden elements,
project isolation, RAG policy and conflict precedence. Reloading reads the saved
value; whitespace and line breaks are preserved and no field character limit is
added. Closed projects expose the value as read-only.

`POST /writer/projects/<id>/reference-bible/` validates `ReferenceBibleForm` and
calls `save_reference_bible`, which locks the project, checks that it is writable,
and updates only `reference_bible` and `updated_at`. An explicitly empty value
clears the field; a missing field is rejected. Saving does not synchronize
chapters, generate text, build contracts, access Drive, write manuscript files,
or change sources, merges, Final or Intake records.

`build_chapter_request` reads the saved project field into the global continuity
context without truncation or additional persistent copies. When the saved field
is empty, it uses the built-in global contract as fallback. The narrative
contract exposes the effective Bible in its reference section and uses this
conflict precedence: Reference Bible, continuity, direction, outline,
references, RAG and then model knowledge. Saving the Bible does not create a
snapshot or modify earlier contracts. Provider context limits still apply to
generation; saving long text is independent of those limits.

Focused verification on disposable PostgreSQL 16 with pgvector:

```bash
python web/manage.py test pipeline.test_writer_reference_bible pipeline.test_intake_library
python web/manage.py check
python web/manage.py makemigrations --check --dry-run
python web/manage.py migrate --check
python -m compileall -q web/writer web/pipeline/test_writer_reference_bible.py
git diff --check
bash scripts/ci/check_repo_hygiene.sh
python scripts/ci/scan_tracked_secrets.py
```

Base: `fix/intake-writer-existing-library` at `e2d67318`. Reverting this focused
change restores the previous UI and request builder; the existing stored field
and operator data remain in place. No migration rollback is needed.

Validation on 2026-10-01: 21 focused tests passed on PostgreSQL 16 with pgvector,
including the unchanged Intake/library tests. A clean migration, Django check,
migration drift/pending checks, compileall, diff check, hygiene and tracked
secret scan passed. Against the approved base, the filled-context regression
failed while the empty-context compatibility test passed. A read-only operator
schema query confirmed the existing TEXT column and applied migration 0013.
The broad suite and remote CI were not rerun for this focused change; no inherited
failures or dependencies were repaired. No operator records were written during
validation. Security impact is limited to a CSRF-protected POST following the
existing Writer access policy and closed-project guard.
