# Writer workflow

The Writer uses only the loopback local model runtime. Configure
`GAIDEN_LOCAL_MODEL_URL` (default `http://127.0.0.1:11434`),
`GAIDEN_LOCAL_MODEL`, and `GAIDEN_LOCAL_EMBEDDING_MODEL`. The service refuses
remote endpoints and rejects Qwen, Phi, GPT, and OpenAI model names. No OpenAI
SDK or external model API is part of the Writer runtime.

Writer retains its existing source selection, normalization, retrieval,
chapter drafting, review, and canonical revision workflow. Those steps prepare
the manuscript inside Writer. After the operator closes a project with a
canonical revision, Intake registers that exact file and SHA-256 as its source.

## After Writer finalization

The Intake asset lane is the only downstream workflow for a Writer canonical.
It uses the Intake bibliographic template and permits frontmatter editing,
cover upload, internal-image upload, and the premium EPUB preview used for
approval. The preview is an approval surface; it does not rewrite the canonical
miolo.

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
