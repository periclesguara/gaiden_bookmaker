#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PATTERNS=(
  "scripts/es/"
  "scripts/legacy_refine/"
  "rebuild_es_from_refine"
  "refine_de"
  "refine_.*2025"
  "polish_en_2025"
)

# Exclude quarantine + docs to avoid false positives from historical artifacts and policy docs.
RG_GLOBS=(
  "--glob" "!docs/**"
  "--glob" "!releases/**"
  "--glob" "!scripts/legacy_refine/**"
  "--glob" "!gaiden/contracts/**"
)

found=0
for pattern in "${PATTERNS[@]}"; do
  if rg -n "${RG_GLOBS[@]}" -- "${pattern}" .; then
    found=1
  fi
 done

if [[ $found -ne 0 ]]; then
  echo "[FAIL] legacy references detected"
  exit 1
fi

echo "[OK] no legacy references found"
