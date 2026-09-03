#!/usr/bin/env bash
set -euo pipefail

: "${GAIDEN_MODEL_ROOT:?Set GAIDEN_MODEL_ROOT to an external absolute directory}"
: "${GAIDEN_QWEN_REVISION:?Set GAIDEN_QWEN_REVISION to an approved 40-character commit SHA}"
: "${GAIDEN_EMBEDDING_REVISION:?Set GAIDEN_EMBEDDING_REVISION to an approved 40-character commit SHA}"

if [[ "$GAIDEN_MODEL_ROOT" != /* ]]; then
  printf 'GAIDEN_MODEL_ROOT must be an absolute path\n' >&2
  exit 2
fi
if [[ ! "$GAIDEN_QWEN_REVISION" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'GAIDEN_QWEN_REVISION must be a 40-character commit SHA\n' >&2
  exit 2
fi
if [[ ! "$GAIDEN_EMBEDDING_REVISION" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'GAIDEN_EMBEDDING_REVISION must be a 40-character commit SHA\n' >&2
  exit 2
fi
if ! command -v hf >/dev/null 2>&1; then
  printf 'The Hugging Face hf CLI is required in the isolated model environment\n' >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
model_root="$(mkdir -p "$GAIDEN_MODEL_ROOT" && cd "$GAIDEN_MODEL_ROOT" && pwd -P)"
case "$model_root/" in
  "$repo_root/"*)
    printf 'Model weights must be stored outside the Git repository\n' >&2
    exit 2
    ;;
esac

hf download Qwen/Qwen3.5-9B --revision "$GAIDEN_QWEN_REVISION" \
  --local-dir "$model_root/Qwen3.5-9B"
hf download Qwen/Qwen3-Embedding-0.6B --revision "$GAIDEN_EMBEDDING_REVISION" \
  --local-dir "$model_root/Qwen3-Embedding-0.6B"

printf 'Models downloaded under %s\n' "$model_root"
