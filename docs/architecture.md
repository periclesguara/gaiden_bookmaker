# Gaiden Bookmaker and Writer architecture

## Physical applications

The repository contains two independently runnable applications:

- `web/`: Gaiden Bookmaker, which owns canonical editorial identity, Intake,
  frontmatter, images, EPUB/PDF assembly and publication artifacts;
- `writer_web/`: Writer, which owns Qwen/RAG drafting, bibles, language
  contracts, chapters, immutable sessions and the outbound body handoff.

The reusable local-Qwen engine lives at top-level `writer_engine/`. It is not
inside `gaiden/`, and Gaiden must not import it. The Gaiden Django project does
not install the `writer` app and exposes only the configurable
`WRITER_APP_URL` navigation link.

The Writer retains Django app label `writer` and its existing migration names,
so moving the source does not rename tables or destroy operator data. The
Writer portal can initially use the existing PostgreSQL database; `WRITER_PG*`
allows a later database split through a separately reviewed migration runbook.

### Intake normalization

Gaiden Intake owns source normalization in `gaiden/normalize.py`. Its local-Qwen
client is independent from `writer_engine` and is limited to classifying
numbered boundary lines for deletion. Deterministic cleanup runs first; Qwen
cannot return replacement prose, inspect the interior outside the configured
windows, or bypass confidence, source-evidence, and maximum-deletion guards.
The RAW source is immutable and each accepted cleanup produces a derived text,
preview, SHA-256 audit report, and explicit removed-line ranges. Details are in
`docs/intake-qwen-normalize.md`.

### Shared source provenance and frontmatter

An explicit RAW upload deterministically extracts stable EPUB/TXT/Markdown
metadata into `Work.source_provenance`. The shared record is read by Intake,
the Manual editor, frontmatter export, and builders, but it never replaces
edition title, author, translator, or other editorial decisions. The canonical
frontmatter section is `title_page.md`; `frontispiece.md` is a byte-identical
legacy alias. Builds place `source_record.md` after copyright and before
edition notes. See `docs/source-provenance-frontmatter.md`.

## Shared immutable sources

Canonical raw originals and normalized bodies remain external artifacts.
PostgreSQL records identity, paths/URIs and SHA-256 values. Writer reads approved
normalized bodies and creates only derived indexes and drafts. It may use an
ephemeral staging copy while vectorizing, but it must not create another
canonical library or modify the source.

## Workflow boundary

1. Writer: Qwen + RAG + bibles + contracts create chapter text.
2. Writer: finalized chapters merge into `body.md`.
3. Writer: `body.md` and `WRITER.HANDOFF.json` are written to the configured
   Google Drive-mounted handoff root with status `AWAITING_GPT_PLUS_WORK`.
4. GPT Plus Work performs the manual editorial revision outside Writer.
5. The returned package must declare `GAIDEN_BODY_READY`.
6. Gaiden Bookmaker imports the verified body after checking SHA-256, skips
   Block 01, and starts at frontmatter/assets before EPUB/PDF assembly.

Writer contains no manual OpenAI translation route and never sends a manuscript
to OpenAI cloud. The `openai` Python SDK is used only as a transport client for
the configured local OpenAI-compatible Qwen generation and embedding endpoints.

## Runtime data boundary

Git stores source, migrations, tests, schemas and documentation. It never stores
manuscripts, bodies, indexes, drafts, handoff packages, EPUB/PDF files, model
weights, credentials, databases or operator exports.

## Quality gates

Both Django projects must pass checks, migration drift, clean PostgreSQL 16 plus
pgvector migration, protected tests, syntax compilation, dependency audit,
repository hygiene and secret scanning.
