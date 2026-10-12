# JSON Contract Layers in Gaiden

Gaiden now has two JSON contract families.

## 1. Editorial Guidance Contracts

These contracts existed before the internal agent runtime. They were designed
to be sent with chunks as prompt guidance for externally hosted OpenAI agents.
They may contain lexical rules, style instructions, translation guidance,
refinement rules, polish rules, collection rules, and editorial memory.

They do not define runtime behavior. They do not select agents, models,
validators, retry policy, audit targets, or internal stages.

These contracts may remain in `data/lexical_rules/` or other legacy guidance
locations. They can later be loaded by the prompt builder as editorial guidance
or lexical memory.

## 2. Internal Agent Operational Contracts

These contracts define internal Gaiden agents. They live under
`data/contracts/`. They are loaded by the Python runtime in
`gaiden/application/agents/`.

They define agent identity, stage, language, model, provider, API, output
policy, validators, retry policy, routing, and audit behavior.

The OpenAI API is used only as an inference engine. Gaiden owns the operational
logic.

## Boundary

Guidance Contract is not Agent Contract.

A legacy guidance contract may enrich the prompt. An internal agent contract
governs execution.

`data/lexical_rules/` remains the place for editorial guidance, lexical memory,
style rules, and legacy payload guidance unless a future migration explicitly
moves a rule into the operational runtime.

`data/contracts/` is reserved for internal agent operational contracts:
registries, agent contracts, language contracts, stage contracts, validators,
and schemas.

Old external agents such as ALAMAGUEDERAZ, ALDEBARAN, YODA MING, KAISER,
CACIQUE, PRISCUS, EL_OBREGON, and LE_GRAND_COULHON are deprecated in the new
Translate flow. They may remain in historical documentation, backups, legacy
code, or migration notes, but they do not control the new runtime.

## Translate EN-US

The UI option remains `Translate`.

When the target language is EN-US, Gaiden resolves the UI request internally as:

- `ui_stage = translate`
- `target_language = en_us`
- `resolved_stage = modernize`
- `language = en_us`
- `agent_id = modernize_en_us_2026`
- `model = gpt-5.4`

This is not a literal translation between languages. It is editorial
modernization into contemporary American English for 2026 readers.

## Runtime Ownership

Gaiden controls contracts, agents, stage routing, language policy, model
selection, validation, retry, audit, and output persistence.

OpenAI executes inference only through the isolated adapter at
`gaiden/infrastructure/openai/responses_client.py`.
