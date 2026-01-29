#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-$HOME/Projetos/gaiden_bookmaker}"
cd "$REPO/web"

# TRAVA hard: se você não setar, ele seta.
export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-5433}"
export PGDATABASE="${PGDATABASE:-gaiden}"
export PGUSER="${PGUSER:-gaiden}"

# Fail-fast: se alguém tentar rodar com outro DB/porta, aborta.
if [[ "$PGPORT" != "5433" || "$PGDATABASE" != "gaiden" ]]; then
  echo "ERROR: gaiden web deve rodar com PGPORT=5433 e PGDATABASE=gaiden"
  echo "Atual: PGPORT=$PGPORT PGDATABASE=$PGDATABASE"
  exit 2
fi

# venv da raiz
PY="$REPO/.venv/bin/python"
exec "$PY" manage.py runserver 0.0.0.0:8000
