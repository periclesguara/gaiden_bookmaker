# Agent-Only Pipeline Contract

Rules
- Refine and Polish are exclusively executed via OpenAI agents.
- Local refine/polish (Python or Node) is forbidden.
- `scripts/legacy_refine/` is historical only and must never be used at runtime.
- `web/pipeline/services/paths.py` is the sole authority for final merge selection.

Official Path
- `merge_translate_<LANG>.txt`
- `split_chapters_for_refine/`
- `return_flow_<lang>_2026.json`
- AGENT
- `merge_refine.txt` / `merge_polish.txt`

Sanity Checks
- `rg -n "scripts/es/|scripts/legacy_refine/|rebuild_es_from_refine|refine_de" web`
- `rg -n "refine_.*2025|polish_en_2025" .`
- `.venv/bin/python web/manage.py check`
