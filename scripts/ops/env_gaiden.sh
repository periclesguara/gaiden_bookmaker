#!/usr/bin/env bash
set -euo pipefail

# Gaiden runtime environment.
# Export secrets in the calling shell or load them through an external secret
# manager before sourcing this file. Never commit real values here.
: "${DJANGO_SECRET_KEY:?DJANGO_SECRET_KEY must be set}"
: "${PGPASSWORD:?PGPASSWORD must be set}"

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-gaiden_portal.settings}"
export DJANGO_DEBUG="${DJANGO_DEBUG:-0}"
export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-localhost,127.0.0.1,[::1]}"

export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-5432}"
export PGDATABASE="${PGDATABASE:-gaiden}"
export PGUSER="${PGUSER:-gaiden}"

# The fingerprint intentionally excludes the password.
export GAIDEN_DB_FINGERPRINT="${GAIDEN_DB_FINGERPRINT:-postgres://${PGUSER}@${PGHOST}:${PGPORT}/${PGDATABASE}}"

printf '[gaiden] active db => %s\n' "${GAIDEN_DB_FINGERPRINT}"
