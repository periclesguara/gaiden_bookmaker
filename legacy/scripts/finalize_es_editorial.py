#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Script para final editorial mecanico (ES) — PASSOS 1..4
# Gera:
#  - data/builds/.../BOOK.MD_FINAL.ready.md
#  - data/builds/.../BOOK.MD_FINAL.final.patch
#  - data/builds/.../BOOK.MD_FINAL.final.report.txt
# Uso:
#   cd /home/periclesguara/Projetos/gaiden_bookmaker
#   python3 scripts/finalize_es_editorial.py

import re
import sys
import subprocess
import pathlib
import shutil

BASE = pathlib.Path("data/builds/book01_the_adventures_of_sherlock_holmes/es")
SRC_CANDIDATES = [
    BASE / "BOOK.MD_FINAL.normalized.md",
    BASE / "BOOK.MD_FINAL",
]
SRC = next((p for p in SRC_CANDIDATES if p.exists()), None)
if not SRC:
    print("Arquivo fonte nao encontrado. Verifique paths:", SRC_CANDIDATES)
    sys.exit(1)

OUT_READY = BASE / "BOOK.MD_FINAL.ready.md"
PATCH = BASE / "BOOK.MD_FINAL.final.patch"
REPORT = BASE / "BOOK.MD_FINAL.final.report.txt"
TERMS = BASE / "BOOK.MD_FINAL.foreign_terms.txt"
ASPELL_LIST = BASE / "BOOK.MD_FINAL.aspell_candidates.txt"

text = SRC.read_text(encoding="utf-8")

# --- PASSO 1: padronizacao mecanica conservadora de dialogos ---
# Contadores
cnt_guille_start = 0
cnt_guille_end = 0
cnt_quote_start = 0

lines = text.splitlines()

out_lines = []
for L in lines:
    # 1) guillemet de abertura no inicio -> em-dash
    m = re.match(r"^(\s*)«\s*—?", L)
    if m:
        L = re.sub(r"^(\s*)«\s*—?", r"\1—", L)
        cnt_guille_start += 1
    # 2) linhas comecando com straight quotes " or '  -> em-dash (dialogo)
    m2 = re.match(r"^\s*[\"\']\s*—?", L)
    if m2:
        L = re.sub(r"^\s*[\"\']\s*—?", r"—", L)
        cnt_quote_start += 1
    # 3) ocorrencias «— internals -> —
    L, _n1 = re.subn(r"«\s*—", "—", L)
    # 4) remocao conservadora de » fechando fala no fim da linha
    L, n2 = re.subn(r"»\s*$", "", L)
    cnt_guille_end += n2
    # 5) remover guillemets soltos que envolvem exatamente uma fala entre travessoes
    L, _n3 = re.subn(r"—\s*«\s*", "—", L)
    L, _n4 = re.subn(r"\s*»\s*—", " —", L)
    # 6) garantir nao comecarem linhas com ASCII quote after spaces
    L = re.sub(r"(?m)^[ \t]+“", "—", L)
    out_lines.append(L)

normalized_text = "\n".join(out_lines)

# safety: ensure all block dialogues start with em-dash — when line begins with dash-like chars
# Replace lines that begin with guillemet later (catch any remaining)
remaining_guille_start = len(re.findall(r"(?m)^\s*«", normalized_text))
remaining_guille_end = len(re.findall(r"»", normalized_text))
remaining_start_quotes = len(re.findall(r"(?m)^\s*[\"\']", normalized_text))

# --- PASSO 2: termos estrangeiros (lista para revisao editorial) ---
# Heuristica conservadora: identificar tokens not containing Spanish diacritics and with ASCII letters,
# capitalized or common English-looking words. Export unique sorted list for manual review.
tokens = set(re.findall(r"\b[A-Za-z][A-Za-z]{2,}\b", normalized_text))

# filter likely Spanish words out by simple heuristic: words with accented vowels likely Spanish -> keep others
def looks_foreign(w):
    if re.search(r"[\u00e1\u00e9\u00ed\u00f3\u00fa\u00fc\u00f1\u00c1\u00c9\u00cd\u00d3\u00da\u00dc\u00d1]", w):
        return False
    # ignore words that are all lowercase in body -> keep proper nouns and candidates
    if w.islower():
        return False
    # ignore single-letter initials
    if len(w) < 3:
        return False
    # very common Spanish words to ignore (small whitelist)
    spanish_common = {
        "Las",
        "Los",
        "El",
        "La",
        "Por",
        "De",
        "En",
        "Con",
        "Que",
        "Esta",
        "Su",
        "Se",
    }
    if w in spanish_common:
        return False
    return True

foreign_candidates = sorted({t for t in tokens if looks_foreign(t)})

TERMS.write_text("\n".join(foreign_candidates), encoding="utf-8")

# --- PASSO 3: verificacao ortografica conservadora ---
# Try to run aspell (script will not apply corrections automatically)
aspell_available = shutil.which("aspell") is not None
aspell_ok = False
aspell_msgs = []
misspellings = []

if aspell_available:
    try:
        # first check if spanish dictionary installed
        p = subprocess.run(["aspell", "dicts"], capture_output=True, text=True)
        dicts = p.stdout.split()
        if any(d.lower().startswith("es") or d.lower() == "spanish" for d in dicts):
            # run aspell list
            proc = subprocess.run(
                ["aspell", "--encoding=utf-8", "-l", "es", "list"],
                input=normalized_text,
                text=True,
                capture_output=True,
            )
            words = set(w for w in proc.stdout.splitlines() if w.strip())
            misspellings = sorted(words)
            aspell_ok = True
            ASPELL_LIST.write_text("\n".join(misspellings), encoding="utf-8")
        else:
            aspell_msgs.append(
                "aspell encontrado, mas sem listas de idioma 'es' instaladas."
            )
    except Exception as e:
        aspell_msgs.append("Erro ao executar aspell: " + str(e))
else:
    aspell_msgs.append("aspell nao encontrado no PATH.")

# Correcoes automaticas 100% inequivocas: map vazio por seguranca (voce pode preencher)
corrections_map = {
    # "MantaQues t.": "MantaQuest.",  # exemplo (deixar comentado/ vazio)
}
applied_corrections = []
if corrections_map:
    t = normalized_text
    for k, v in corrections_map.items():
        if k in t:
            t = t.replace(k, v)
            applied_corrections.append((k, v))
    normalized_text = t

# write ready file
OUT_READY.write_text(normalized_text, encoding="utf-8")

# --- gerar patch unificado (git diff --no-index) ---
try:
    p = subprocess.run(
        ["git", "diff", "--no-index", "-U3", str(SRC), str(OUT_READY)],
        capture_output=True,
        text=True,
    )
    diffout = p.stdout
    if not diffout:
        # if git not available or no diff, create fallback minimal patch
        diffout = ("*** " + str(SRC) + "\n+++ " + str(OUT_READY) + "\n")
    PATCH.write_text(diffout, encoding="utf-8")
except Exception as e:
    # fallback: write simple file
    PATCH.write_text("ERROR generating patch: " + str(e), encoding="utf-8")

# --- Relatorio final curto ---
report_lines = []
report_lines.append("SRC: %s" % SRC)
report_lines.append("OUT_READY: %s" % OUT_READY)
report_lines.append("PATCH: %s" % PATCH)
report_lines.append("")
report_lines.append("PASSO 1 — Padronizacao de dialogos")
report_lines.append(
    "  guillemets aberturas transformadas (estimado): %d" % cnt_guille_start
)
report_lines.append(
    "  guillemets fechamentos removidos (estimado): %d" % cnt_guille_end
)
report_lines.append(
    "  linhas comecando com aspas convertidas (estimado): %d" % cnt_quote_start
)
report_lines.append(
    "  ocorrencias restantes « no arquivo pronto: %d" % remaining_guille_start
)
report_lines.append(
    "  ocorrencias restantes » no arquivo pronto: %d" % remaining_guille_end
)
report_lines.append(
    "  linhas comecando com \" (aspas) restantes: %d" % remaining_start_quotes
)
report_lines.append("")
report_lines.append(
    "PASSO 2 — Termos estrangeiros detectados (arquivo): %s" % TERMS
)
report_lines.append("  candidatos encontrados: %d" % len(foreign_candidates))
for w in foreign_candidates[:200]:
    report_lines.append("    " + w)
if len(foreign_candidates) > 200:
    report_lines.append("    ... (truncado)")
report_lines.append("")
report_lines.append("PASSO 3 — Ortografia objetiva")
if aspell_ok:
    report_lines.append(
        "  aspell(es) executado com dicionario es — candidatos listados em: %s"
        % ASPELL_LIST
    )
    report_lines.append(
        "  numero de tokens apontados por aspell: %d" % len(misspellings)
    )
else:
    report_lines.append("  aspell indisponivel ou sem dicionario ES. Mensagens: ")
    for m in aspell_msgs:
        report_lines.append("    - " + m)
    report_lines.append(
        "  Foi gerada apenas a lista heuristica de termos estrangeiros para revisao manual."
    )
report_lines.append("")
report_lines.append("PASSO 4 — Entrega")
report_lines.append(
    "  arquivo final pronto (sem mudancas estilisticas): %s" % OUT_READY
)
report_lines.append("  patch gerado: %s" % PATCH)
report_lines.append("")
report_lines.append(
    "Correcoes automaticas aplicadas (mapa explicito): %d"
    % len(applied_corrections)
)
for k, v in applied_corrections:
    report_lines.append("  REPLACE: %s -> %s" % (k, v))

REPORT.write_text("\n".join(report_lines), encoding="utf-8")

print("Finalizado. Arquivos gerados:")
print(" -", OUT_READY)
print(" -", PATCH)
print(" -", TERMS)
print(" -", REPORT)
if aspell_ok:
    print(" -", ASPELL_LIST)
else:
    print("aspell nao disponivel ou sem dict ES; veja relatorio for next steps.")
