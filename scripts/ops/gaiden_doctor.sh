#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Preferir env local se existir (ignorado pelo git)
if [ -f "backups/db/local_pg_env.sh" ]; then
  # shellcheck disable=SC1091
  source "backups/db/local_pg_env.sh"
fi

# Resolver interpretador Python (ordem: venv ativo -> .venv raiz -> web/.venv -> python do sistema)
PY="python"
if [ -n "${VIRTUAL_ENV:-}" ]; then
  PY="python"
elif [ -x "${ROOT_DIR}/.venv/bin/python" ]; then
  PY="${ROOT_DIR}/.venv/bin/python"
elif [ -x "${ROOT_DIR}/web/.venv/bin/python" ]; then
  PY="${ROOT_DIR}/web/.venv/bin/python"
else
  echo "ERROR: Nenhum venv encontrado."
  echo "Ative um venv (source .venv/bin/activate) OU crie ${ROOT_DIR}/.venv/bin/python."
  exit 1
fi

echo "== gaiden_doctor =="
echo "Repo: $ROOT_DIR"
echo "Branch: $(git branch --show-current)"
echo "Python: $PY"
echo "PGHOST=${PGHOST:-} PGPORT=${PGPORT:-} PGDATABASE=${PGDATABASE:-} PGUSER=${PGUSER:-}"

cd web

echo
echo "-- Django check --"
"$PY" manage.py check

echo
echo "-- Migrations (editorial/pipeline) --"
"$PY" manage.py showmigrations editorial | tail -n 25
"$PY" manage.py showmigrations pipeline  | tail -n 25

echo
echo "-- Smoke: required fields exist in ORM --"
"$PY" manage.py shell -c "
from editorial.models import Edition, PipelineArtifact
from pipeline.models import BookEditionTemplate
print('Edition.copyright_text:', Edition._meta.get_field('copyright_text'))
print('Edition.editorial_name:', Edition._meta.get_field('editorial_name'))
print('Edition.edition_copyright_holder:', Edition._meta.get_field('edition_copyright_holder'))
print('Edition.language_variant:', Edition._meta.get_field('language_variant'))
print('PipelineArtifact.status:', PipelineArtifact._meta.get_field('status'))
print('PipelineArtifact.sha256:', PipelineArtifact._meta.get_field('sha256'))
print('BookEditionTemplate.edition_year:', BookEditionTemplate._meta.get_field('edition_year'))
print('BookEditionTemplate.editorial_name:', BookEditionTemplate._meta.get_field('editorial_name'))
print('BookEditionTemplate.edition_copyright_holder:', BookEditionTemplate._meta.get_field('edition_copyright_holder'))
"

echo
echo "-- Smoke: export frontmatter ES (book_0001) --"
"$PY" manage.py export_frontmatter --book-code book_0001 --language es

echo
echo "OK: gaiden_doctor passou."
