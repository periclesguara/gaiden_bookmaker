#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

export DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:-dev-only}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-gaiden_portal.settings}"
export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-5432}"
export PGDATABASE="${PGDATABASE:-gaiden}"
export PGUSER="${PGUSER:-gaiden}"
export PGPASSWORD="${PGPASSWORD:-gaiden}"

source ./.venv/bin/activate

exec python web/manage.py runserver 127.0.0.1:8000
