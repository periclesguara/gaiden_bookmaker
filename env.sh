#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

: "${DJANGO_SECRET_KEY:?DJANGO_SECRET_KEY must be set by the environment or an approved secret manager}"
export DJANGO_SETTINGS_MODULE=gaiden_portal.settings
export PGHOST=127.0.0.1
export PGPORT="${PGPORT:-5432}"
export PGDATABASE=gaiden
export PGUSER=gaiden
: "${PGPASSWORD:?PGPASSWORD must be set by the environment or an approved secret manager}"

source ./.venv/bin/activate

# Local model runtime. Select installed models without cloud-provider routing.
export GAIDEN_LOCAL_MODEL_URL="http://127.0.0.1:11434"
: "${GAIDEN_LOCAL_MODEL:?Set GAIDEN_LOCAL_MODEL to an installed local model}"
: "${GAIDEN_LOCAL_EMBEDDING_MODEL:?Set GAIDEN_LOCAL_EMBEDDING_MODEL to an installed local embedding model}"
