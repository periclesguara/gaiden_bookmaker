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

### Metadata and RinoBooks boundary

`editorial.EditionMetadata` is the canonical source for storefront, SEO,
rights, and commercial metadata for one edition. The relation is one-to-one
with `editorial.Edition`; `Work.code` remains the canonical `book_code` and is
displayed read-only instead of being duplicated. Draft rows may be incomplete,
while normalized `slug` and `edition_code` values become database-unique as
soon as they are supplied.

Metadata validation is a service-layer rule. Critical errors block EPUB/PDF
export, full Gaiden export, manifest generation, and RinoBooks delivery. SEO
length recommendations are warnings and do not block. A separate package
preflight runs EPUBCheck and resolves the cover before writing
`BOOK.MANIFEST.json` or making an outbound request.

The RinoBooks bridge sends a multipart HTTPS request only after an explicit
operator action. It carries manifest v2 plus the validated EPUB and cover, and
accepts only a receiver response whose status is `DRAFT`. The bridge has no
publication action and does not control public pages, sitemap submission, or
indexing. See `docs/rinobooks-publication.md` for the full contract.

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
application adds source manifests, story projects, creative bibles, chapter
parameters, immutable generation sessions, and explicit editorial finalization.
Supporting-cast continuity is versioned: RAG-assisted updates create immutable
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
