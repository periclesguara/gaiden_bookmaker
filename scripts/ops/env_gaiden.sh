#!/usr/bin/env bash
set -euo pipefail

# === GAIDEN ACTIVE DB (single source of truth) ===
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-gaiden_portal.settings}"
export DJANGO_SECRET_KEY="${DJANGO_SECRET_KEY:?DJANGO_SECRET_KEY not set}"

export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-5433}"
export PGDATABASE="${PGDATABASE:-gaiden}"
export PGUSER="${PGUSER:-gaiden}"
export PGPASSWORD="${PGPASSWORD:-gaiden}"

# DB fingerprint obrigatório: se não bater, aborta.
export GAIDEN_DB_FINGERPRINT="${GAIDEN_DB_FINGERPRINT:-postgres://${PGUSER}@${PGHOST}:${PGPORT}/${PGDATABASE}}"

echo "[gaiden] active db => ${GAIDEN_DB_FINGERPRINT}"
