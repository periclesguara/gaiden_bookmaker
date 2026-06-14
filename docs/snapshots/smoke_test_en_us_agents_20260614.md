# EN-US Agents Smoke Test - 2026-06-14

Branch: `feature/en-us-modernize-refine-agents`

Base: `origin/main` at `d8b2821e`

## Scope

Validated the generic internal-agent flow:

`Translate EN-US -> modernize_en_us_2026 -> Refine EN-US -> refine_en_us_2026`

No `book_0026`, Collections, stashes, or legacy external agents were used.

## Input

- Book ID: `smoke_en_us_agents_2026`
- Input path: `data/chunks/smoke_en_us_agents_2026/en/ch_001_chunk_001.txt`

## Outputs

- Modernize output: `data/translated/smoke_en_us_agents_2026/en_us/ch_001_chunk_001.txt`
- Refine output: `data/refined/smoke_en_us_agents_2026/en_us/ch_001_chunk_001.txt`

These runtime outputs are ignored and were not committed.

## Audits

- Modernize audit: `docs/audit/agent_runs/smoke_en_us_agents_2026/en_us/modernize/ch_001_chunk_001.run.json`
- Refine audit: `docs/audit/agent_runs/smoke_en_us_agents_2026/en_us/refine/ch_001_chunk_001.run.json`

These audit outputs are ignored and were not committed.

## Contracts

- `data/contracts/agent_registry.json`
- `data/contracts/agents/modernize_en_us_2026.agent.json`
- `data/contracts/agents/refine_en_us_2026.agent.json`
- `data/contracts/languages/en_us_2026.json`
- `data/contracts/stages/modernize.json`
- `data/contracts/stages/refine.json`
- `data/contracts/validators/archaic_terms.en_us_2026.json`
- `data/contracts/validators/no_meta_commentary.json`
- `data/contracts/validators/length_ratio_basic.json`

## Model

- Requested/default model: `gpt-5.4`
- Modernize audit model: `gpt-5.4`
- Refine audit model: `gpt-5.4`

## Adapter Finding

The first real modernize call failed because the Responses API rejected `temperature` for `gpt-5.4`, `gpt-5.4-mini`, and `gpt-5`.

Fix committed separately:

- `0aa83bb3 Handle Responses models without temperature`

After the adapter retried without `temperature`, both stages passed.

## Modernize Result

- UI stage: `translate`
- Resolved stage: `modernize`
- Agent: `modernize_en_us_2026`
- Validation: `passed`
- Retries: `0`
- Archaic grep: no matches
- Meta-commentary grep: no matches

Short output excerpt:

```text
Chapter 1 — Smoke Test

You have seen what no one should behold.

Why do you remain silent when your heart knows the truth?
```

## Refine Result

- UI stage: `refine`
- Resolved stage: `refine`
- Agent: `refine_en_us_2026`
- Validation: `passed`
- Retries: `0`
- Archaic grep: no matches
- Meta-commentary grep: no matches

Short output excerpt:

```text
Chapter 1 — Smoke Test

You have seen what no one should ever see.

Why do you stay silent when your heart knows the truth?
```

Qualitative check:

- The heading was preserved.
- Residual archaic terms were removed.
- Meaning and sequence were preserved.
- Refine improved flow and sentence clarity without turning the passage into a new adaptation.
- No excessive contractions were introduced.

## Test Results

Before smoke:

- `python -m compileall gaiden`: passed
- Internal-agent pytest suite: `30 passed`
- JSON contracts: valid

After smoke:

- `python -m compileall gaiden`: passed
- Internal-agent pytest suite: `30 passed`

## Overall Result

PASSED

The branch proves the generic internal-agent path:

`Translate EN-US -> Modernize EN-US -> Refine EN-US`

with JSON contracts, Python runtime, OpenAI Responses API, validators, and audit logs.

## Recommendations

1. Open a PR from `feature/en-us-modernize-refine-agents`.
2. Keep `fix/book-0026-ptbr-refine-patch` retired from architecture work.
3. Add a small unit test for the Responses adapter temperature fallback in a follow-up.
4. After PR review, run a second smoke with a longer public-domain chunk.
