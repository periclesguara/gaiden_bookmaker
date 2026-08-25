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

### Intake provenance schema compatibility

Automated Drive Intake creates every `Work` with an explicit empty
`source_provenance` object. The compatibility migration adds that JSON column
only when it is absent, initializes pre-existing rows with `{}`, and preserves
the physical column on reverse migration. This keeps this Intake branch safe on
both clean databases and environments where the approved provenance migration
already created the column; it never makes provenance nullable or replaces an
existing record.

For a registered Google Drive folder, the Intake dashboard links directly to
the file-selection table filtered by its immutable batch code. This transition
is read-only: it neither repeats the import nor changes the selected source.
Each file still requires an explicit preview or POST selection before it enters
the editorial-production block.

### Block 01 normalization and chapter translation

New production work uses the versioned Block 01 chain: immutable RAW,
Qwen block classification under `gaiden_normalize_decision_v2`, deterministic
`normalized_body.txt`, validated `structure-map.json`, chapter split, Drive
transport, progressive returns, validation, and canonical merge. The reusable
normalizer is in `gaiden/application/normalization`; Django persistence and
artifact publication are coordinated by `pipeline.services.block01_normalize`.
Views invoke Qwen only from the explicit Normalize POST action.

`gaiden/application/translation/chapter_splitter.py` consumes the validated
Normalize structure map for new jobs and proves that source units reconstruct
`normalized_body` exactly. `pipeline.services.chapter_translation` retains the
same persistence, transport, return-validation, and merge boundary. New rows
use `gaiden_manual_translation_job_v3`; v1 monolithic jobs and v2
`heading_clean` chapter jobs remain readable and operational without conversion.
No new v3 flow creates or requires a `heading_clean` artifact.

The `PipelineArtifact` index records RAW, normalized body, structure map, and
the completed translation. Google Drive is transport only. A v3 job advances
to `BLOCK_01_COMPLETE` only after every unit is validated and the deterministic
merge has atomically published `translated_body.txt`,
`translation-manifest.json`, and `qa-report.json`. Frontmatter belongs to Block
02 and is blocked from the moment Normalize stages v3 provenance until the job
reaches `BLOCK_01_COMPLETE`. Intake and Normalize do not generate frontmatter. Operational details
are in `docs/runbooks/block-01-normalize-qwen-v2.md`.

### Writer engine phase 1

`gaiden/writer_engine/` is a reusable, UI-independent draft-generation layer.
It owns deterministic corpus discovery and chunking, atomic vector-index
serialization, retrieval, prompt boundaries, Qwen client coordination, and
originality checks. It does not own canonical promotion or final builds.

Generation and embeddings use separate configurable OpenAI-compatible local
services. Qwen3.5-9B is the default generation model and
Qwen3-Embedding-0.6B is the default retrieval model. Corpus files, model
weights, indexes, requests, drafts, and audit sidecars remain external runtime
artifacts. The reusable engine remains independent of Django. The `writer`
application opens without Django authentication and adds source manifests,
story projects, creative bibles, chapter parameters, immutable generation
sessions, and explicit editorial finalization.
Its external file-treatment and deployment contract is defined in
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
