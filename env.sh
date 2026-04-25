#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

export DJANGO_SECRET_KEY=dev-only
export DJANGO_SETTINGS_MODULE=gaiden_portal.settings
export PGHOST=127.0.0.1
export PGPORT="${PGPORT:-5432}"
export PGDATABASE=gaiden
export PGUSER=gaiden
export PGPASSWORD=gaiden

source ./.venv/bin/activate
