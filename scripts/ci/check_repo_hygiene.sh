#!/usr/bin/env bash
set -euo pipefail

violations=0

while IFS= read -r -d '' path; do
  case "$path" in
    .gaiden_secrets|.gaiden_secrets.*|.env|.env.*)
      if [[ "$path" != ".env.example" ]]; then
        printf 'forbidden tracked environment/credential file: %s\n' "$path" >&2
        violations=1
      fi
      ;;
    *.sqlite|*.sqlite3|*.db|*.epub)
      printf 'forbidden tracked runtime/binary artifact: %s\n' "$path" >&2
      violations=1
      ;;
    *.pem|*.key|credentials*.json|service-account*.json)
      printf 'forbidden tracked credential file: %s\n' "$path" >&2
      violations=1
      ;;
  esac
done < <(git ls-files -z)

if (( violations != 0 )); then
  exit 1
fi

printf 'repository hygiene check passed\n'
