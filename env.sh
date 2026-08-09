#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

export DJANGO_SECRET_KEY=dev-only
export DJANGO_SETTINGS_MODULE=gaiden_portal.settings
export PGHOST=127.0.0.1
export PGPORT="${PGPORT:-5432}"
export PGDATABASE=gaiden
export PGUSER=gaiden
export PGPASSWORD=gaiden

source ./.venv/bin/activate

# Local Qwen runtime served by the loopback-only gaiden-ollama container.
export GAIDEN_EMBEDDING_BASE_URL="http://127.0.0.1:8001/v1"
export GAIDEN_EMBEDDING_API_KEY="ollama"
export GAIDEN_EMBEDDING_MODEL="qwen3-embedding:0.6b"
export GAIDEN_QWEN_BASE_URL="http://127.0.0.1:8001/v1"
export GAIDEN_QWEN_API_KEY="ollama"
export GAIDEN_QWEN_MODEL="qwen3.5:9b-q4_K_M"
export GAIDEN_QWEN_THINKING="0"
