#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/periclesguara/Projetos/gaiden_bookmaker"
PARTS="$ROOT/data/chunks/book_0001/refine_es_01/parts"
OUTDIR="$ROOT/data/chunks/book_0001/refine_es_01/parts_out"
FINAL="$ROOT/data/chunks/book_0001/refine_es_01/refined_es_mx_2025.txt"
CLEAN="$ROOT/data/chunks/book_0001/refine_es_01/merged_es_2025.clean.txt"

mkdir -p "$OUTDIR"

# 1) clean determinístico
python scripts/es/clean_es_merged.py

# 2) split por capítulo/conto
python scripts/es/split_by_story_heading_clean.py

# 2b) validações estruturais de headings (robustas)
if ! grep -nE "^12\\.\\s+" "$CLEAN" | head -n 1 >/dev/null; then
  echo "FAIL: não achei heading do cap 12 (linha começando com '12.')"
  exit 1
fi
H12="$(grep -nE "^12\\.\\s+" "$CLEAN" | head -n 1)"
echo "INFO: heading 12 -> $H12"
echo "$H12" | grep -Eiq "COBRE" || echo "WARN: heading 12 não contém 'COBRE' (título pode variar)"

# sanity: parts não podem ter linhas 1 (lixo)
echo "== parts line count (min check) =="
bad=0
for f in "$PARTS"/part_*.txt; do
  n=$(wc -l < "$f" | tr -d ' ')
  printf "%-20s %s\n" "$(basename "$f")" "$n"
  first_line=$(head -n 1 "$f" | tr -d '\r')
  if [ -z "$first_line" ]; then
    echo "ERROR: $(basename "$f") starts with blank line."
    bad=1
  fi
  if [ "$n" -lt 20 ]; then
    bad=1
  fi
done
if [ "$bad" -eq 1 ]; then
  echo "ERROR: alguma part está pequena demais (<20 linhas). Abortando pra não queimar grana."
  exit 1
fi

# load secrets (OPENAI_API_KEY etc)
set -a
source "$ROOT/.gaiden_secrets"
set +a

# refine each part (cache/resume + validation)
for f in "$PARTS"/part_*.txt; do
  base="$(basename "$f" .txt)"
  out="$OUTDIR/${base}.refined.txt"
  echo "Refine: $f -> $out"
  # Skip if cached and valid
  if [ -f "$out" ]; then
    python - <<PY
import sys, re, pathlib
inp = pathlib.Path("$f").read_text(encoding="utf-8")
outp = pathlib.Path("$out").read_text(encoding="utf-8")
forbidden = ["El texto continúa", "A partir de este punto", "sin alteraciones", "[...]"]
low = outp.lower()
if any(x.lower() in low for x in forbidden):
    sys.exit(2)
if len(outp) < int(len(inp) * 0.85):
    sys.exit(3)
PY
    status=$?
    if [ "$status" -eq 0 ]; then
      echo "OK cached: $out"
      continue
    fi
    echo "Cached output invalid, re-running: $out"
  fi

  node "$ROOT/scripts/es/refine_one_part.mjs" --in "$f" --out "$out"

  python - <<PY
import sys, re, pathlib
inp = pathlib.Path("$f").read_text(encoding="utf-8")
outp = pathlib.Path("$out").read_text(encoding="utf-8")
forbidden = ["El texto continúa", "A partir de este punto", "sin alteraciones", "[...]"]
low = outp.lower()
if any(x.lower() in low for x in forbidden):
    print("ERROR: forbidden disclaimer in", "$out")
    sys.exit(2)
if len(outp) < int(len(inp) * 0.85):
    print("ERROR: output too short vs input in", "$out")
    sys.exit(3)
PY
done

# merge
cat "$OUTDIR"/part_*.refined.txt > "$FINAL"

echo "OK merged refined -> $FINAL"

# cheap validations
echo "== validations =="
if grep -ni "El texto continúa" "$FINAL" | head -n 1; then
  echo "ERROR: achou disclaimer 'El texto continúa...' (LLM cortou). Abortando."
  exit 1
fi

if [ "$(ls -1 "$PARTS"/part_*.txt | wc -l | tr -d ' ')" -ne 12 ]; then
  echo "ERROR: numero de parts diferente de 12."
  exit 1
fi

python - <<'PY'
import pathlib, re, sys
final = pathlib.Path("/home/periclesguara/Projetos/gaiden_bookmaker/data/chunks/book_0001/refine_es_01/refined_es_mx_2025.txt")
text = final.read_text(encoding="utf-8").rstrip()
if not text:
    sys.exit("ERROR: refined file empty.")
last_char = text[-1]
if last_char not in ".!?…»”\"'":
    sys.exit("ERROR: last paragraph does not appear to end cleanly.")
PY

echo "OK: no disclaimer"
echo "HEAD:"
head -n 20 "$FINAL"
echo "TAIL:"
tail -n 20 "$FINAL"
