# Gaiden Pipeline Operating Model (2026)

1) Two orthogonal expansion modes

A) MULTILANGUAGE MODE (horizontal)
- One book
- Many languages (EN, FR, ES, IT, PT-BR, DE)
- Images shared (anchored to book_<ID>/EN)
- Single project, multiple outputs

B) SEQUENTIAL MODE (vertical)
- Many books
- One language
- Each book has its own images and cover
- Jobs sent ONE BY ONE through Runner Matrix

2) Rule of execution
- Never batch OpenAI calls.
- Every job = one PipelineRunItem.
- Queue guarantees cost control and crash safety.

3) Agent-only invariant
- Refine and Polish NEVER run locally.
- UI only sends and receives via return_flow contracts.
- merge_refine.txt / merge_polish.txt are the only accepted outputs.
