#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="${ROOT}/.venv"

if [[ ! -x "${VENV}/bin/python" ]]; then
  echo "ERROR: venv not found at ${VENV}" >&2
  exit 2
fi

source "${VENV}/bin/activate"
which python
python -V
python web/manage.py runserver 0.0.0.0:8000
