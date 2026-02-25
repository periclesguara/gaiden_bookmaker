#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
OUT="docs/snapshots/preupdate_${STAMP}"
mkdir -p "$OUT"

tar -czf "$OUT/assets_${STAMP}.tgz" \
  data/books data/builds gaiden/frontmatter_store docs/audit/runs 2>/dev/null || true

sha256sum "$OUT/assets_${STAMP}.tgz" > "$OUT/assets_${STAMP}.sha256"
echo "OK pre-update snapshot at $OUT"
