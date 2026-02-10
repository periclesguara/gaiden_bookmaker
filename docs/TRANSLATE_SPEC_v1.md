GAIDEN MATRIX — TRANSLATE STAGE SPECIFICATION (v1)
Date: 2026
Scope: TRANSLATE ONLY (no cleanear, no refine, no polish)
Engine: OpenAI GPT-5.2
Input unit: CHUNKS
Output unit: TRANSLATED CHUNKS + MERGE
Language base: English source

============================================================
1) FUNDAMENTAL DESIGN DECISIONS
------------------------------------------------------------
1. There is NO single universal JSON for translation rules.
2. Translation rules are NOT embedded as data artifacts.
3. Translation rules are a SPEC, referenced by contracts.
4. Contracts select:
   - mode (multilanguage OR multibook)
   - target language(s)
   - execution order (queue)
5. Artifacts (chunks, translated chunks, merges) are passive.
6. Behavior is governed by:
   - this SPEC
   - the selected contract
   - the OpenAI model (GPT-5.2)

============================================================
2) TRANSLATION MODES (STRICT SEPARATION)
------------------------------------------------------------

MODE A — MULTILANGUAGE
--------------------------------
Definition:
- ONE book
- MANY target languages
- ALWAYS English source

Allowed target languages:
1) English → Modern English (2026)
2) German
3) French
4) Spanish
5) Portuguese (PT-BR neutral)
6) Italian

Execution rule:
- Languages are processed IN QUEUE
- One language at a time
- Same ruleset applied to all languages
- Only language-specific conventions differ

Use case:
- Canonical book propagation to multiple markets

--------------------------------

MODE B — MULTIBOOK
--------------------------------
Definition:
- MANY books
- ONE target language
- ALWAYS English source

Execution rule:
- Books are processed IN QUEUE
- One book at a time
- Same ruleset applied to all books
- No cross-book context sharing

Use case:
- Batch production
- Matrix Gaiden scale operation

============================================================
3) UNIVERSAL TRANSLATION RULES (MANDATORY)
------------------------------------------------------------
These rules apply to ALL languages and BOTH modes.

A) MEANING & STRUCTURE
--------------------------------
- Do NOT invent content.
- Do NOT rewrite the story.
- Do NOT summarize.
- Do NOT expand.
- Do NOT remove information.
- Preserve paragraph boundaries.
- Preserve heading/chapter structure.
- Preserve narrative order.

B) MODERNIZATION (ALLOWED & REQUIRED)
--------------------------------
Translate into **modern language (2026)**.

This includes:
- Reducing redundancy caused by literal or machine translation.
- Removing archaic constructions that harm readability.
- Updating orthography to current standards.
- Applying modern grammatical conventions.
- Applying modern punctuation conventions.

Modernization MUST:
- Preserve meaning
- Preserve tone
- Preserve narrative intent

C) SENTENCE MANAGEMENT
--------------------------------
- Very long sentences MAY be split.
- Splitting is allowed ONLY when:
  - clarity improves
  - no information is lost
  - logical order is preserved
  - cause/effect relationships are intact
- Do NOT merge separate sentences.
- Do NOT create stylistic flourishes.

D) CADENCE & FLUENCY
--------------------------------
- Prioritize natural cadence in the target language.
- Avoid literal calques.
- Ensure lexical and grammatical continuity between chunks.
- Each chunk must read as a natural continuation of the previous one.

E) LEXICON & STYLE
--------------------------------
- Use updated dictionaries and orthographic standards.
- Avoid archaic vocabulary unless explicitly present in the source.
- Slang and insults:
  - Use minimally.
  - Only when present in the source.
  - Modernize gently (no exaggeration).

F) NAMES, DATES, REFERENCES
--------------------------------
- Proper names: NEVER change.
- Dates, places, historical references: NEVER change.
- Numeric meaning must remain identical.

============================================================
4) OUTPUT RULES (ABSOLUTE)
------------------------------------------------------------
- Output ONLY the translated text.
- No comments.
- No explanations.
- No headers like “Translated text”.
- No metadata.
- No JSON wrappers.
- No Markdown.
- No footnotes.

============================================================
5) CHUNK CONTINUITY REQUIREMENT
------------------------------------------------------------
Although translation is chunk-based:

- The model MUST assume that:
  - previous chunk exists
  - next chunk exists
- Translation must maintain:
  - lexical continuity
  - grammatical flow
  - logical progression

Do NOT introduce hard openings or artificial closures inside chunks.

============================================================
6) MODEL & ENGINE CONSTRAINTS
------------------------------------------------------------
- Translation engine: OpenAI GPT-5.2 ONLY
- Temperature: low (deterministic behavior)
- The model is used as:
  - translation engine
  - light modernization engine
- It is NOT used as:
  - editor
  - summarizer
  - stylist
  - commentator

============================================================
7) RELATION TO OTHER STAGES
------------------------------------------------------------
- CLEANEAR: NOT ACTIVE in this phase
- REFINE / POLISH: NOT ACTIVE in this phase
- NORMALIZE & CHUNKS are ASSUMED STABLE
- TRANSLATE must NOT compensate for upstream errors

============================================================
8) CONTRACT RESPONSIBILITY
------------------------------------------------------------
Contracts are responsible for:
- Selecting MODE (multilang OR multibook)
- Selecting target language(s)
- Selecting execution order (queue)
- Selecting artifacts (chunks in / translated chunks out)

Contracts do NOT:
- Redefine translation behavior
- Override this ruleset

============================================================
END OF SPEC
