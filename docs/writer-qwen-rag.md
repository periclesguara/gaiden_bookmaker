# Gaiden Writer: Qwen3.5 and Sherlock RAG

## Geração atual: OpenAI Luna

O Writer usa exclusivamente `gpt-5.6-luna` em `https://api.openai.com/v1`
(Responses API), reutilizando `OPENAI_API_KEY` do ambiente ou do carregador de
segredos já existente. A chave nunca deve ser inserida em código ou documentação.
O modelo e o endpoint são próprios do Writer: `GAIDEN_QWEN_*`,
`GAIDEN_DEFAULT_MODEL` e `OPENAI_BASE_URL` não alteram essa rota.

Geração, merge, revisão e reparos que usam o gerador do Writer enviam o contexto
necessário à OpenAI. Os embeddings e índices RAG existentes continuam locais e
não precisam ser recriados. Qwen não redige: um Qwen3.5-4B local atualiza a
memória narrativa somente depois do merge. Um Phi-4 Mini local executa a
auditoria lógica. Outros módulos e seus modelos permanecem independentes.

Cada chamada usa `reasoning.effort=none`, `store=false`, timeout de 180 segundos,
limite de 256–32768 tokens e zero retries de transporte. Parâmetros de amostragem
locais (inclusive a temperatura solicitada pelo crítico) não são enviados.
Saídas vazias, incompletas ou erros interrompem o fluxo sem registrar o corpo da
resposta de erro. A aprovação humana e a preservação de sessões permanecem.

Reinicie o processo Django do Writer para carregar a mudança e aplique a migração
aditiva `writer.0006_chapter_narrative_memory` após backup e ensaio em clone
descartável. Não reinicie uma geração em andamento. Testes automatizados usam
mocks, sem chamadas pagas ou locais.

Referência: https://developers.openai.com/api/docs/models/gpt-5.6-luna

## Referência histórica do runtime Qwen

As instruções de geração Qwen abaixo são históricas e não configuram o Writer
atual. A configuração de embeddings e os procedimentos de indexação continuam
relevantes. Não remova pesos ou índices usados por outros módulos.


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

- OpenAI `gpt-5.6-luna` creates scene drafts and merges them into a chapter.
- Qwen3.5-4B extracts the cumulative narrative state after each merge.
- Phi-4 Mini Instruct audits causality, temporality, and coherence.
- Qwen3-Embedding-0.6B creates multilingual retrieval vectors.
- OpenAI-compatible loopback APIs serve generation and embeddings. The
  workloads may use separate services or one Ollama service with separate
  model names.
- Gaiden writes an atomic JSONL vector index under external storage. Phase 2 may
  move the same contract to PostgreSQL/pgvector after retrieval is accepted.
- Every output remains DRAFT and receives an audit sidecar with the model,
  retrieved chunk IDs, and scores.

The Qwen memory model is not an embedding model and does not write narrative
prose. Writing, memory, logical audit, and semantic retrieval remain separately
configured and independently replaceable.

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

### Local Ollama workstation

The currently verified workstation uses the official Ollama container pinned
to digest
`sha256:b88c73ace3e115f8ec53dc8761ae1c0aabfa675406e3681786b98757ce050f42`.
It publishes only `127.0.0.1:8001`, persists weights in the named Docker volume
`gaiden_ollama_models`, and keeps all model data outside the repository.

The installed model tags are:

- `qwen3-embedding:0.6b` for vectors;
- `qwen3.5:9b-q4_K_M` for chapter generation.

Configure both clients against the same loopback API:

```bash
export GAIDEN_EMBEDDING_BASE_URL=http://127.0.0.1:8001/v1
export GAIDEN_EMBEDDING_API_KEY=ollama
export GAIDEN_EMBEDDING_MODEL=qwen3-embedding:0.6b
export GAIDEN_QWEN_BASE_URL=http://127.0.0.1:8001/v1
export GAIDEN_QWEN_API_KEY=ollama
export GAIDEN_QWEN_MODEL=qwen3.5:9b-q4_K_M
export GAIDEN_QWEN_THINKING=0
```

The local API key is a client compatibility value, not a credential, and is
safe only because the port is restricted to loopback. The GTX 1060 has enough
VRAM for the embedding model, while the 9B writing model may split layers
between GPU and system memory. Expect a model reload when alternating between
retrieval and generation. Recreate or upgrade the container only after checking
that the named volume exists; deleting that volume removes the downloaded
weights but not manuscripts, indexes, or database records.

## Complete Sherlock indexing

The source root contains only operator-approved canonical Sherlock UTF-8
Markdown or text. Frontmatter, covers, generated translations, and EPUBs do not
belong in the index.

```bash
export GAIDEN_EMBEDDING_BASE_URL=http://127.0.0.1:8001/v1
export GAIDEN_EMBEDDING_API_KEY=ollama
export GAIDEN_EMBEDDING_MODEL=qwen3-embedding:0.6b

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
export GAIDEN_QWEN_BASE_URL=http://127.0.0.1:8001/v1
export GAIDEN_QWEN_API_KEY=ollama
export GAIDEN_QWEN_MODEL=qwen3.5:9b-q4_K_M
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

### Local Writer index path fix

The chapter service converts the database string index path to `Path` before loading.
The local desktop launcher loads `~/.config/gaiden/writer.env` so both Ollama
clients retain their configured model tags after restart. No index rebuild is needed.
\nThe creative base includes separate bibles for the protagonist, supporting characters, antagonist, locations, and world. The supporting-characters bible is required before chapter generation and is included in every session continuity context.\n
