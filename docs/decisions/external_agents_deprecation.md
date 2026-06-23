# External Hosted Agents Deprecation

Gaiden no longer uses externally hosted OpenAI agents as the operational source
of truth for editorial stages.

Legacy external agents such as ALDEBARAN, YODA MING, CACIQUE, KAISER, PRISCUS,
EL_OBREGON, LE_GRAND_COULHON, and ALAMAGUEDERAZ are deprecated for new runtime
execution.

The new runtime uses internal JSON-first agents defined under `data/contracts/`
and executed by `gaiden/application/agents/`.

OpenAI remains the inference provider, but no longer hosts the editorial agent
logic.

The new model is:

```text
JSON contracts -> Python runtime -> OpenAI inference -> validators -> audit
```

Legacy agent names may remain in historical documentation, backups, legacy code,
or editorial guidance contracts, but must not be used to route, execute,
validate, or audit new pipeline stages.

`data/lexical_rules/` is not agent runtime. It may continue to hold editorial
guidance, lexical memory, legacy payload guidance, and style rules.

`data/contracts/` is the source of truth for internal operational agents,
including registries, agent contracts, language contracts, stage contracts,
validators, and schemas.

## Migration Policy

The external-agent deprecation does not authorize a big-bang migration of every
language at once.

New internal Translate agents must be introduced incrementally:

- one language per sprint
- one operational package per stage
- one contract set per agent
- one smoke test per language/stage route

EN-US modernize/refine is the first internal runtime package. Future Translate
routes such as PT-BR, ES, DE, FR, and IT require their own agent contract,
language policy, validators, resolver entry, tests, and smoke evidence before
they are considered active internal runtime.

Until each language is migrated, legacy names may remain only as deprecated
history, compatibility labels, or guarded legacy workflow references. They must
not be used to route new internal execution.
