#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"

cd "${ROOT}"

if [[ -z "${DJANGO_SECRET_KEY:-}" ]]; then
  echo "[preflight] ERROR: DJANGO_SECRET_KEY is not set"
  echo "[preflight] Fix: export DJANGO_SECRET_KEY=dev-only-\$(date +%s) (dev) or use your secrets manager (prod)"
  exit 2
fi

# Load canonical env (DB + fingerprint) when available.
if [[ -f "${ROOT}/scripts/ops/env_gaiden.sh" ]]; then
  # shellcheck source=/dev/null
  source "${ROOT}/scripts/ops/env_gaiden.sh"
else
  export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-gaiden_portal.settings}"
  export PGHOST="${PGHOST:-127.0.0.1}"
  export PGPORT="${PGPORT:-5433}"
  export PGDATABASE="${PGDATABASE:-gaiden}"
  export PGUSER="${PGUSER:-gaiden}"
  export PGPASSWORD="${PGPASSWORD:-gaiden}"
  export GAIDEN_DB_FINGERPRINT="${GAIDEN_DB_FINGERPRINT:-postgres://${PGUSER}@${PGHOST}:${PGPORT}/${PGDATABASE}}"
  echo "[preflight] WARN: scripts/ops/env_gaiden.sh not found; using inline defaults"
fi

PY="${ROOT}/.venv/bin/python"
if [[ ! -x "${PY}" ]]; then
  PY="$(command -v python || command -v python3)"
fi

echo "[preflight] python: $(${PY} --version 2>/dev/null || true)"
echo "[preflight] cwd: ${ROOT}"
echo "[preflight] db:  ${GAIDEN_DB_FINGERPRINT}"

# Explicit fingerprint sanity in shell to fail fast even before command-level preflight exists.
expected_fp="postgres://${PGUSER}@${PGHOST}:${PGPORT}/${PGDATABASE}"
if [[ "${GAIDEN_DB_FINGERPRINT}" != "${expected_fp}" ]]; then
  echo "[preflight] ERROR: ACTIVE DB MISMATCH"
  echo "[preflight] Expected: ${expected_fp}"
  echo "[preflight] Got:      ${GAIDEN_DB_FINGERPRINT}"
  exit 3
fi

# Quick Django sanity
"${PY}" "${ROOT}/web/manage.py" check

# Optional: verify GAIDEN_LIBRARY links if present
if [[ -d "${ROOT}/data" ]]; then
  if [[ -L "${ROOT}/data/books" ]]; then
    echo "[preflight] data/books -> $(readlink "${ROOT}/data/books")"
  fi
  if [[ -L "${ROOT}/data/builds" ]]; then
    echo "[preflight] data/builds -> $(readlink "${ROOT}/data/builds")"
  fi
fi

# Ensure key dirs exist (no heavy writes)
mkdir -p "${ROOT}/docs/audit" "${ROOT}/docs/snapshots"

echo "[preflight] OK"
