DEFAULT_REFERENCE_BIBLE = """REFERENCE BIBLE - GLOBAL CONTRACT

1. CHARACTERS
   - character rules
   - personality
   - voice
   - abilities
   - limitations
   - relationships
   - continuity status

2. WORLD
   - period
   - geography
   - society
   - politics
   - economy
   - technology
   - culture
   - historical constraints

3. SETTING
   - atmosphere
   - architecture
   - locations
   - environmental rules
   - recurring locations

4. VOICE AND LANGUAGE
   - narrator
   - POV
   - language variant
   - dialogue rules
   - vocabulary
   - tone

5. GENRE RULES
   - mystery
   - thriller
   - adventure
   - horror
   - non-fiction
   - etc.

6. REFERENCE AUTHORS / WORKS
   - reference
   - purpose
   - allowed use
   - forbidden use

7. CONTINUITY
   - previous events
   - permanent consequences
   - recurring characters
   - unresolved events

8. TIMELINE
   - story year
   - chronology
   - historical boundaries
   - forbidden anachronisms

9. CANON
   - series canon
   - project canon
   - source canon
   - research only

10. FORBIDDEN ELEMENTS
   - things the model must never introduce

11. PROJECT ISOLATION
   - allowed projects
   - forbidden projects
   - shared references

12. RAG POLICY
   - which corpus may be searched
   - metadata filters
   - reference roles
   - contamination rules

13. CONFLICT PRECEDENCE

   BIBLE
   > CONTINUITY
   > DIRECTION
   > OUTLINE
   > REFERENCES
   > RAG
   > MODEL KNOWLEDGE
"""


REFERENCE_BIBLE_PRECEDENCE = [
    "reference_bible",
    "continuity_ledger",
    "chapter_direction",
    "story_outline",
    "reference_metadata",
    "source_rag",
    "model_knowledge",
]


def effective_reference_bible(project) -> str:
    return project.reference_bible or DEFAULT_REFERENCE_BIBLE
