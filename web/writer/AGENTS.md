# Writer operator rules

- OpenAI Luna writing is orchestrated only by Django's Writer service. Codex may
  implement/test guardrails and dispatch one explicitly authorized Django run.
- Do not coordinate individual generations, poll GPU/model progress in a loop,
  rewrite prompts between sessions, restart the model, or automatically retry
  failed generations. This avoids consuming Codex credits during writing.
- Preserve completed sessions and their text. Never clear sessions to resume.
- Duplicate dispatch must stop before model work; repetition must fail closed.
- Django now owns up to three candidate attempts per pending session (initial +
  two repairs), as authorized by the Writer validation specification. Transport
  errors, uncertain critic results, and exhausted attempts stop for manual review.
  Codex must not coordinate those attempts. A new run after failure requires
  operator authorization. QA approval is still a DRAFT, never editorial finalization.
- Do not create Codex heartbeat monitors for Writer generation unless requested.
