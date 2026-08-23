# Intake normalization with local Qwen

## Purpose

The Intake `Normalize` action removes source-repository wrappers before the
manuscript reaches split, translation, or editorial generation. It covers
Project Gutenberg, Standard Ebooks, Internet Archive/archive.org, Distributed
Proofreaders, Google Books, HathiTrust, Wikisource, and equivalent license,
scan, OCR, transcription, and download notices.

This step removes source boilerplate. It does not remove Gaiden's internal
language contracts and it does not rewrite the author's text.

## Safety model

Normalization has two ordered layers:

1. deterministic v2 cleanup handles known Gutenberg markers, residual metadata,
   blank lines, and the existing Roman-numeral compatibility rule;
2. local Qwen inspects only a numbered window at the beginning and end of that
   deterministic text and returns deletion ranges as JSON.

Qwen never returns replacement prose. Gaiden applies a proposed range only when
all of these checks pass:

- the response uses `normalize_cleanup_v1`;
- confidence is at least the configured threshold;
- every line is inside the inspected boundary window;
- the selected text contains source-boilerplate evidence;
- the total deletion remains below the configured ratio;
- the resulting manuscript is not empty.

Title pages, author and translator credits, dedications, epigraphs, contents,
introductions, prefaces, notes, original colophons, headings, captions, and body
text are explicitly protected by the prompt. Structural validation remains the
final authority; a confident model answer cannot bypass it.

There is no silent fallback. If the local endpoint fails or a deletion violates
a guardrail, Normalize fails and leaves the prior normalized artifact and
pipeline state unchanged.

## Configuration

The Gaiden client is independent from `writer_engine` and may point to the same
local Qwen service through its own variables:

```text
GAIDEN_NORMALIZE_QWEN_BASE_URL=http://127.0.0.1:8000/v1
GAIDEN_NORMALIZE_QWEN_API_KEY=placeholder
GAIDEN_NORMALIZE_QWEN_MODEL=Qwen/Qwen3.5-9B
GAIDEN_NORMALIZE_BOUNDARY_LINES=180
GAIDEN_NORMALIZE_MIN_CONFIDENCE=0.80
GAIDEN_NORMALIZE_MAX_REMOVED_RATIO=0.35
```

Placeholder keys are accepted only for loopback endpoints. A remote endpoint
requires HTTPS and a real API key. Normal operation must keep manuscripts on
the approved local model service.

## Operation

From the Intake edition flow, upload the RAW source and select **Normalize com
Qwen**. The matrix flow uses the same implementation:

The explicit RAW upload also extracts stable source provenance into the shared
`Work` record. This deterministic extraction runs before normalization and does
not call Qwen. Opening Intake with GET performs neither operation. See
`docs/source-provenance-frontmatter.md` for the schema and Manual/build use.

```bash
python -m gaiden.normalize book_0042 en
```

The command reads `data/raw/<book>/<language>/source.txt` or `source.md` and
writes derived artifacts under `data/normalized/<book>/<language>/`:

- `<book>_<language>_v2.txt` for downstream compatibility;
- `normalize_report.json` with model, source kinds, hashes, counts, and applied
  line ranges;
- `normalize_preview.txt` for operator inspection.

The report never stores removed text. RAW remains untouched. Normalized files
are derived runtime artifacts and can be deleted and rebuilt from the canonical
RAW source; they must not be committed to Git.

## Acceptance and recovery

Before Split, inspect the preview and report. If legitimate material was
selected, do not raise the deletion limit first. Preserve RAW, correct the
source-specific cue or model decision, rerun Normalize, and compare the SHA-256
and ranges. No database migration or persistent-data rewrite is part of this
feature.
