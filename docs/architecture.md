# Gaiden architecture

## Active application

Gaiden has two deliberate layers:

- `gaiden/` contains reusable editorial processing code and command-line tools;
- `web/` contains the Django operator application.

The Django project is `web/gaiden_portal` and installs two first-party apps:

- `editorial`: canonical works, editions, contributors, metadata, frontmatter,
  and editorial artifacts;
- `pipeline`: jobs, runs, source selection, build paths, and operator flows.

Reusable business logic belongs in service modules. Views should coordinate
HTTP input and output rather than implement filesystem or editorial rules.

### Modular operator navigation

The default-branch dashboard is the single operator entrypoint and separates
navigation into Writer, Intake, Bookmaker Manual/AI, and finalized-project
areas. These links target the current default-branch routes: the modern Writer,
the canonical edition list, pipeline jobs, and the read-only DONE projection on
the dashboard. Loading the dashboard is read-only and never starts Writer,
Qwen, embedding, RAG, or pipeline work.

The former `refactor/module-boundaries-writer-manual-intake-v1` branch and
`ac9e69c6` were historical UI references only. They are not an integration
base, and their retired applications, models, migrations, routes, and Writer
implementation are not part of the active architecture.

### Writer engine phase 1

`gaiden/writer_engine/` is a reusable, UI-independent draft-generation layer.
It owns deterministic corpus discovery and chunking, atomic vector-index
serialization, retrieval, prompt boundaries, Qwen client coordination, and
originality checks. It does not own canonical promotion or final builds.

Generation and embeddings use separate configurable OpenAI-compatible local
services. Qwen3.5-9B is the default generation model and
Qwen3-Embedding-0.6B is the default retrieval model. Corpus files, model
weights, indexes, requests, drafts, and audit sidecars remain external runtime
artifacts. The reusable engine remains independent of Django. The staff-only `writer`
application adds source manifests, story projects, Fiction creative bibles,
Nonfiction operator text and per-chapter source guidance, chapter parameters,
immutable generation sessions, and explicit editorial finalization. Nonfiction
generation preserves the operator thesis, accepts citations only for retrieved
chunk IDs, and renders those IDs as auditable source notes. Supporting-cast
continuity is versioned for Fiction: RAG-assisted updates create immutable
revision records, while each generation session stores the exact cast snapshot
and hash that governed its output. Its external file-treatment and deployment
contract is defined in
`docs/writer-workflow.md`.

## Runtime data boundary

Git is the source-of-truth for code, migrations, tests, small fixtures,
configuration templates, and documentation. It is not the canonical store for:

- manuscripts or chunks;
- generated EPUB, PDF, Markdown, images, or covers;
- databases, backups, logs, credentials, or operator exports.

Runtime artifacts belong under the configured external storage. Paths under
`data/` are compatibility locations and must remain ignored unless a small,
reviewed fixture is intentionally added under a dedicated fixture directory.

## Compatibility inventory

The following names indicate compatibility code, not automatic deletion
candidates:

- modules suffixed `_2025.py`;
- `setup_translate_2025.sh`;
- book-specific build and normalization scripts;
- `web/gaiden_portal/settings_sqlite.py`.

Before removal, each item must have its callers searched, its replacement
identified, and a rollback commit recorded. New production paths must not add
dependencies on these compatibility modules.

## Quality gates

### Post-Intake preservation and EPUB evidence

The first Issue #43 correction is documented in
[the implementation report](audits/post-intake-43.md). The existing QA service
now flags source-reference and contents text for review rather than removing
it with regular expressions. Manuscript promotion preserves exact UTF-8 bytes,
stores a digest-addressed snapshot, and refuses to overwrite different existing
content. Both normal and alternate-language approval use this guard.

The default-branch Pandoc exporter consumes the existing frontmatter metadata
context instead of inventing a book title. Explicit EPUBCheck validation records
structured, hash-bound evidence and fails closed on missing tools, malformed
reports, changed files, errors, and unreviewed warnings. Full-book production
validates the exact newly exported file before proceeding to PDF; it does not
select an old `BOOK.EPUB3` alias by precedence. Standalone export remains a
candidate-generation operation, not editorial approval.

These are partial integrity controls, not the complete fifteen editorial gates.
The premium renderer running on the operator's feature branch is absent from
this approved baseline and has not been integrated. No model-backed literary,
semantic image, cover OCR, or reader-rendering approval is implied by a
technical EPUBCheck pass. See the report for unresolved acceptance criteria.

Every pull request must pass:

1. repository hygiene and current-tree secret scanning;
2. Python and shell syntax checks;
3. PostgreSQL 16 plus pgvector clean migration;
4. migration drift and pending-migration checks;
5. at least the protected Django test baseline;
6. dependency vulnerability auditing.

The minimum test count is a regression guard, not a coverage target. New or
changed behavior must add direct tests.

## Change boundaries

Migration repair, runtime-data cleanup, compatibility-code retirement, and
feature integration are separate review units. Persistent data is never
deleted merely to make model state match source code.
