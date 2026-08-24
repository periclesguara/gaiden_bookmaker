# Chapter translation through Google Drive v2

## Scope and ownership

The v2 workflow is:

```text
heading_clean
→ chapter split
→ split validation
→ Google Drive transport
→ progressive returns
→ unit validation
→ deterministic local merge
→ final QA
→ translation ready
```

The splitter is reusable Gaiden application code. The Django `pipeline` app
persists job state and coordinates Drive. Writer is not changed and does not
own translation jobs. Google Drive is a transport connector only. The original
RAW source and `Work.source_provenance` remain the bibliographic source of
truth.

## Compatibility

`ManualTranslationJob.schema_version` distinguishes the paths:

- `gaiden_manual_translation_job_v1`: existing monolithic input and return;
- `gaiden_manual_translation_job_v2`: ordered chapter units and deterministic
  merge.

An existing v1 row is never converted automatically. The unique
edition/target-language identity remains in force, so Gaiden cannot create a
second job identity for the same book and target language.

## Split and Qwen

The splitter reads only `heading_clean`, records character offsets and a
SHA-256 for every unit, and verifies that concatenating all source ranges
reconstructs the source exactly. Content before the first heading becomes
`0000__preliminaries.txt`. Chapters above the hard limit are split only at
paragraph boundaries.

The limits are configured with:

```text
GAIDEN_CHAPTER_SPLIT_ALERT_CHARACTERS=30000
GAIDEN_CHAPTER_SPLIT_HARD_LIMIT_CHARACTERS=60000
GAIDEN_CHAPTER_SPLIT_QWEN_CONFIDENCE=0.85
```

Qwen is available only through the explicit POST action shown after a
deterministic split requires review. Its versioned contract is
`data/contracts/translation/chapter_detection_v1.json`. Qwen receives source
length, source hash, and structural line evidence; it cannot rewrite text.
Suggested offsets are rejected unless they cover the complete source without
gaps or overlaps and their headings occur inside the suggested ranges.

## Drive contract

V2 publishes immutable files under:

```text
04_TRANSLATION_JOBS/<book_code>/<target>/
├── input/
│   ├── translation-job.json
│   ├── style-contract.json
│   └── chapters/*.txt
└── return/
    ├── RETURN_HERE.txt
    ├── translation-return.template.json
    ├── translation-return.json
    └── chapters/*.txt
```

The exporter reads an existing remote file before treating it as a no-op. A
different hash at an expected path becomes `CONFLICT`; it is never overwritten
silently. Unknown return names, path traversal, wrong language suffixes,
non-UTF-8 files, empty files, and divergent second returns are rejected.
The importer requires `translation-return.json` as soon as chapter returns are
present and verifies its job ID, book code, target language, source hash, exact
unit IDs, expected paths, duplicate entries, and any non-empty return hashes.

## Return validation and merge

Return-size and paragraph-count review thresholds are configured with:

```text
GAIDEN_CHAPTER_RETURN_MIN_SIZE_RATIO=0.45
GAIDEN_CHAPTER_RETURN_MAX_SIZE_RATIO=1.80
GAIDEN_CHAPTER_RETURN_MIN_PARAGRAPH_RATIO=0.50
GAIDEN_CHAPTER_RETURN_MAX_PARAGRAPH_RATIO=2.00
```

Validation also checks headings, Markdown fences, TODO markers, model messages
or refusals, duplicated/missing units, source hashes, and recurring proper
names. Size, paragraph, and proper-name anomalies require review rather than
automatic approval.

Merge order comes from `TranslationUnit.sequence`, not filenames. The final
file is written atomically and indexed as a `PipelineArtifact` with SHA-256.
An existing divergent final or `merge_translate.txt` blocks promotion. The
merge manifest and QA report stay in the configured runtime storage and are
not committed to Git.

## Migrations and persistent data

This feature adds:

- `editorial.0021`: SHA-256 and two stage choices on `PipelineArtifact`;
- `pipeline.0021`: backward-compatible v2 fields on `ManualTranslationJob`,
  plus `TranslationUnit` and `TranslationJobEvent`.

Existing v1 rows receive blank/default v2 fields and keep their status,
edition, target language, source hash, and return fields. Neither migration
removes a table or column.

Before applying to a persistent environment:

1. create and list a timestamped `pg_dump`;
2. restore it into disposable PostgreSQL 16 with pgvector;
3. record row counts and hashes for existing `ManualTranslationJob`, Edition,
   Work, and provenance fields;
4. run `migrate --plan`, apply migrations in the disposable clone, and compare
   the recorded values;
5. run representative v1 and v2 ORM writes;
6. obtain explicit operator approval for the operational migration.

Do not reverse these migrations on a populated environment: reversal would
drop v2 tables and fields. Application rollback should deploy the preceding
code while leaving the additive schema in place. Restore the verified dump
only under a separately approved database-recovery runbook.

## Validation commands

```bash
python -m unittest -v tests.test_chapter_translation_splitter
python web/manage.py check
python web/manage.py makemigrations --check --dry-run
python web/manage.py test pipeline.test_chapter_translation
python web/manage.py test pipeline.test_chapter_translation_migrations
python web/manage.py test pipeline.test_drive_intake
```

Ordinary tests must mock Qwen and Drive. No test should contact a paid API,
publish a book, or use operational credentials.
