# Collection Handoff To Pipeline

- a Collection só pode usar o pipeline padrão depois de gerar o source unificado
- sequência formal:
  - `COLLECTION_MERGED`
  - `COLLECTION_READY_FOR_PIPELINE`
  - `COLLECTION_PIPELINE_RUNNING`
- antes disso, a Collection não entra no fluxo de Book
- o artefato de handoff é o merged final em `data/collections/.../merged/`
