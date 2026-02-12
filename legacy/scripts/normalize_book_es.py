#!/usr/bin/env python3
# Script: normaliza diálogos em BOOK.MD_FINAL (ES)
# - cria BOOK.MD_FINAL.normalized.md
# - cria patch git-style BOOK.MD_FINAL.normalize-dialogue.patch (diff -u --no-index)
# Uso:
#   cd /home/periclesguara/Projetos/gaiden_bookmaker
#   python3 scripts/normalize_book_es.py

import re
import sys
import subprocess
import pathlib

orig = pathlib.Path(
    "data/builds/book01_the_adventures_of_sherlock_holmes/es/BOOK.MD_FINAL"
)
out = pathlib.Path(str(orig) + ".normalized.md")
patch = pathlib.Path(str(orig) + ".normalize-dialogue.patch")

if not orig.exists():
    print("Arquivo nao encontrado:", orig)
    sys.exit(1)

text = orig.read_text(encoding="utf-8")

# Contagens iniciais
lines = text.splitlines()
count_start_guille = sum(1 for L in lines if re.match(r"^\s*«", L))
count_end_guille = sum(1 for L in lines if re.search(r"»\s*$", L))

# Transformacoes conservadoras (dialogos em blocos)
t = text

# 1) «—  -> — (guillemets imediatamente antes de travessao)
t, n1 = re.subn(r"«\s*—", "—", t)

# 2) linhas que comecam com « -> travessao longo
t, n2 = re.subn(r"(?m)^(?P<indent>\s*)«\s*", r"\g<indent>—", t)

# 3) remover » apenas quando fecham blocos (fim de linha)
t, n3 = re.subn(r"»(?=\s*$)", "", t, flags=re.MULTILINE)

# 4) remover » antes de quebra de linha (casos raros)
t, n4 = re.subn(r"»(?=\n)", "", t)

# 5) limpar espacos excessivos apos travessao
t, n5 = re.subn(r"(?m)^(?P<indent>\s*)—\s+", r"\g<indent>—", t)

# 6) fallback de «— internos
t, n6 = re.subn(r"«\s*—", "—", t)

# Salva versao normalizada
out.write_text(t, encoding="utf-8")

# Gera diff unificado (nao depende de repo git)
try:
    p = subprocess.run(
        ["git", "diff", "--no-index", "-U3", str(orig), str(out)],
        capture_output=True,
        text=True,
    )
    diffout = p.stdout
except Exception as e:
    diffout = ""
    print("Aviso: erro ao gerar diff com git:", e)

if diffout:
    patch.write_text(diffout, encoding="utf-8")
    print("Patch gerado em:", patch)
else:
    patch.write_text(
        "--- " + str(orig) + "\n+++ " + str(out) + "\n",
        encoding="utf-8",
    )
    print("Patch criado sem diff (verificar manualmente):", patch)

# Relatorio
print("--- Resumo rapido ---")
print("Linhas que comecavam com « :", count_start_guille)
print("Linhas que terminavam em » :", count_end_guille)
print("Substituicoes «— -> —     :", n1)
print("Substituicoes inicio «    :", n2)
print("Remocoes de » finais      :", n3 + n4)
print("Outras limpezas           :", n5 + n6)
print("Arquivo normalizado       :", out)
print("Patch salvo               :", patch)
