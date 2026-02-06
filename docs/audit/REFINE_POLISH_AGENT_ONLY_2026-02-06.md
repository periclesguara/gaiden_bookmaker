# Refine/Polish Legacy Removal (Agent-Only)

Date: 2026-02-06

Summary
- Removed local gaiden refine/polish modules (2025 variants).
- UI/Django refine and polish now execute agent return flow only.
- Legacy refine scripts moved to scripts/legacy_refine/.

Behavior
- Refine: runs gaiden.return_splits with return_flow_<lang>_2026.json and copies output to build_dir/merge_refine.txt.
- Polish: runs gaiden.return_splits with the same contract and copies output to build_dir/merge_polish.txt.
- EN return_en flow remains available as a separate action (unchanged).

Compatibility
- Split/translate/merge generation and contracts were not modified.
- Output naming and build_dir paths remain unchanged.
