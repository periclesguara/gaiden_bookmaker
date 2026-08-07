# Gaiden Writer: Qwen3.5 and Sherlock RAG

## Status and phase boundary

Phase 1 provides local model services, complete corpus indexing, retrieval, and
chapter-draft generation. It deliberately adds no Django form, database field,
or canonical-promotion action. Those belong to phase 2 after an operator
acceptance run.

The current main branch does not contain unpublished Sherlock manuscripts. Git
is not their storage layer. The complete corpus must therefore be supplied from
approved external storage when the index command runs. Indexing fails if any
discovered text source produces no chunk.

## Architecture

- Qwen3.5-9B creates chapter drafts.
- Qwen3-Embedding-0.6B creates multilingual retrieval vectors.
- Separate OpenAI-compatible loopback endpoints serve generation and embeddings.
- Gaiden writes an atomic JSONL vector index under external storage. Phase 2 may
  move the same contract to PostgreSQL/pgvector after retrieval is accepted.
- Every output remains DRAFT and receives an audit sidecar with the model,
  retrieved chunk IDs, and scores.

Qwen3.5 is not used as an embedding model. Generation and semantic retrieval
are separate workloads and remain independently replaceable.

## Runtime layout

Example:

```text
/srv/gaiden/
  models/
    Qwen3.5-9B/
    Qwen3-Embedding-0.6B/
  corpora/sherlock-canon/
  writer/
    indexes/sherlock.jsonl
    drafts/
```

These directories must stay outside the Git checkout.

## Model download

Use an isolated model-serving environment. Resolve and review the immutable
40-character revisions shown by the official Hugging Face repositories, then:

```bash
export GAIDEN_MODEL_ROOT=/srv/gaiden/models
export GAIDEN_QWEN_REVISION=<approved-40-character-model-commit>
export GAIDEN_EMBEDDING_REVISION=<approved-40-character-model-commit>
bash scripts/writer/download_qwen_models.sh
```

The downloader rejects branch names such as main, relative destinations, and
destinations inside the repository. Model weights are never committed.

## Serving

Qwen documents vLLM and SGLang as compatible servers. Install the chosen server
in a dedicated locked environment, not in Gaiden's Django environment. Example
after operational review:

```bash
vllm serve /srv/gaiden/models/Qwen3.5-9B \
  --host 127.0.0.1 --port 8000 --language-model-only \
  --max-model-len 32768 --reasoning-parser qwen3

vllm serve /srv/gaiden/models/Qwen3-Embedding-0.6B \
  --host 127.0.0.1 --port 8001 --task embed
```

Keep endpoints on loopback unless TLS, authentication, firewall policy, and
real API keys are configured. The client refuses placeholder keys for a
non-loopback endpoint.

## Complete Sherlock indexing

The source root contains only operator-approved canonical Sherlock UTF-8
Markdown or text. Frontmatter, covers, generated translations, and EPUBs do not
belong in the index.

```bash
export GAIDEN_EMBEDDING_BASE_URL=http://127.0.0.1:8001/v1
export GAIDEN_EMBEDDING_API_KEY=placeholder
export GAIDEN_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B

python -m gaiden.writer_engine index \
  --source-root /srv/gaiden/corpora/sherlock-canon \
  --index /srv/gaiden/writer/indexes/sherlock.jsonl
```

The final JSON reports source count, non-zero chunk count, model, and dimension.
Compare its source count with the corpus manifest. A rebuild atomically replaces
the complete index and never appends a partial mixture.

```bash
python -m gaiden.writer_engine query \
  --index /srv/gaiden/writer/indexes/sherlock.jsonl \
  --query "Watson observes an apparently impossible locked room" \
  --top-k 8
```

Query output contains retrieval metadata, not complete passages.

## Chapter draft

Create a request outside Git:

```json
{
  "title": "The Locked Observatory",
  "language": "English",
  "brief": "A fair-play mystery driven by one physical clue.",
  "continuity": "Watson narrates. Holmes has not met the suspect before.",
  "point_of_view": "First-person Watson",
  "target_words": 2500
}
```

Run:

```bash
export GAIDEN_QWEN_BASE_URL=http://127.0.0.1:8000/v1
export GAIDEN_QWEN_API_KEY=placeholder
export GAIDEN_QWEN_MODEL=Qwen/Qwen3.5-9B
export GAIDEN_QWEN_THINKING=0

python -m gaiden.writer_engine chapter \
  --index /srv/gaiden/writer/indexes/sherlock.jsonl \
  --request /srv/gaiden/writer/requests/chapter-01.json \
  --output /srv/gaiden/writer/drafts/chapter-01.md
```

The command refuses to overwrite an existing draft or audit sidecar and rejects
an exact 14-word sequence copied from retrieved material.

## Acceptance gate before phase 2

Do not create UI fields until all items pass:

1. corpus manifest and index source counts match;
2. retrieval tests cover character, chronology, location, clue, and tone;
3. five representative briefs produce coherent chapters without long copying;
4. a human accepts continuity and narrative voice;
5. latency and memory fit the actual machine;
6. model revisions and serving environment are locked;
7. recovery is proven by deleting the derived index and rebuilding from the
   untouched corpus.

After that gate, phase 2 may add Django fields, immutable draft versions, review,
approval, and explicit promotion.
