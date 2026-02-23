#!/usr/bin/env python3
# fix_conan_titles_official_v2.py
# Uso:
#   python scripts/ops/fix_conan_titles_official_v2.py \
#     data/books/book_0004/en/runs/v03_fullflow_20260219T213622Z/outputs/agent_shinobi/20260219T215709/book_0004__en__agent_shinobi__MERGED__headingsfix_v1.txt \
#     data/books/book_0004/en/book_0004_refine_clean.md

from __future__ import annotations
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

CANON_TITLES = [
  "O Sleeper, Awake!",
  "A Black Wind Blows",
  "The Cliffs Reel",
  "“What Hell Did You Crawl Out Of?”",
  "The Haunter of the Pits",
  "The Thrust of a Knife",
  "The Rending of the Veil",
  "Dying Embers",
  "“It Is the King or His Ghost!”",
  "A Coin from Acheron",
  "Swords of the South",
  "The Fang of the Dragon",
  "“A Ghost Out of the Past”",
  "The Black Hand of Set",
  "The Return of the Corsair",
  "Black-Walled Khemi",
  "“He Has Slain the Sacred Son of Set!”",
  "“I Am the Woman Who Never Died”",
  "In the Hall of the Dead",
  "Out of the Dust Shall Acheron Arise",
  "Drums of Peril",
  "The Road to Acheron",
]

# Âncoras EXPLÍCITAS (fallback) para capítulos que faltarem.
# Regra: 1 âncora por capítulo (regex), deve casar exatamente 1 vez.
# Preencha com trechos hiper-distintivos do seu texto (não inventa, só copia do arquivo).
ANCHORS = {
  1: r"^The tall candles flickered, casting black, shifting shadows",
  3: r"^The Aquilonian army was drawn up—long, tight ranks of pikemen",
  4: r"^Conan knew nothing of that long ride in Xaltotun’s chariot",
  5: r"^Conan lay still, enduring the weight of his chains and the hopelessness",
  9: r"^Many people passed through the great arched gates of Tarantia",
  10: r"^Not all of his guides entered the chamber",
  13: r"^Soon after sunrise, Conan crossed into Argos",
  18: r"^Conan stared intently at his masked companions",
  19: r"^Conan moved carefully toward the light he had seen",
}

def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def norm_quotes(s: str) -> str:
    # só pra matching; não é “edição”
    return (s.replace("“", '"').replace("”", '"').replace("’", "'"))

def title_to_regex(title: str) -> re.Pattern:
    # casa com ou sem número e pontuação antes (ex: "9 – “IT IS...”")
    t = norm_quotes(title)
    t_esc = re.escape(t)
    # permitir variações de aspas no arquivo
    t_esc = t_esc.replace(r"\"", r"[\"“”]").replace(r"\'", r"[\'’]")
    # permitir espaços/pontuação leve
    return re.compile(
        rf"^\s*(?:\d{{1,2}}\s*[-–—.:]?\s*)?{t_esc}\s*$",
        re.IGNORECASE
    )

def is_allcaps_title_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    # “ALLCAPS-ish” (permite aspas e pontuação)
    letters = re.sub(r"[^A-Za-z]+", "", s)
    if not letters:
        return False
    return letters.isupper()

def main() -> int:
    if len(sys.argv) != 3:
        print("Uso: fix_conan_titles_official_v2.py <input.txt> <output.md>", file=sys.stderr)
        return 2

    inp = Path(sys.argv[1])
    out = Path(sys.argv[2])

    text = inp.read_text(encoding="utf-8").splitlines(True)  # keep newlines
    backup = inp.with_suffix(inp.suffix + f".bak_{utc_stamp()}")
    backup.write_text("".join(text), encoding="utf-8")

    # 1) Promover títulos canônicos quando aparecerem como linha isolada
    patterns = [(i+1, title, title_to_regex(title)) for i, title in enumerate(CANON_TITLES)]

    found = set()
    i = 0
    while i < len(text):
        line = text[i]
        replaced = False

        # já é heading? normaliza se for capítulo
        m = re.match(r"^\s*##\s*Chapter\s+(\d{1,2})\.\s*(.+?)\s*$", line.strip())
        if m:
            ch = int(m.group(1))
            if 1 <= ch <= 22:
                # reescreve com título canônico “oficial”
                text[i] = f"## Chapter {ch}. {CANON_TITLES[ch-1]}\n"
                found.add(ch)
            i += 1
            continue

        for ch, title, rx in patterns:
            if rx.match(norm_quotes(line.strip())):
                text[i] = f"## Chapter {ch}. {CANON_TITLES[ch-1]}\n"
                found.add(ch)

                # matar duplicação imediata abaixo (muito comum: linha ALLCAPS repetida)
                if i+1 < len(text):
                    nxt = text[i+1].strip()
                    if nxt and is_allcaps_title_line(nxt) and (norm_quotes(nxt).lower() == norm_quotes(title).lower()):
                        text[i+1] = ""  # remove
                replaced = True
                break

        if replaced:
            i += 1
            continue

        i += 1

    # remove linhas vazias criadas
    text = [ln for ln in text if ln != ""]

    # 2) Sanity parcial
    headings = [ln for ln in text if ln.startswith("## Chapter ")]
    got = sorted({int(re.match(r"^## Chapter (\d+)\.", ln).group(1)) for ln in headings if re.match(r"^## Chapter (\d+)\.", ln)})

    missing = [n for n in range(1, 23) if n not in got]

    # 3) Fallback por âncora (SE e SOMENTE SE faltar capítulo)
    if missing:
        if not ANCHORS:
            print(f"[ERR] faltam capítulos {missing} e ANCHORS está vazio. Preencha ANCHORS no script.", file=sys.stderr)
            return 1

        joined = "".join(text)
        for ch in missing:
            if ch not in ANCHORS:
                print(f"[ERR] faltou âncora para capítulo {ch}.", file=sys.stderr)
                return 1
            rx = re.compile(ANCHORS[ch], re.MULTILINE)
            hits = list(rx.finditer(joined))
            if len(hits) != 1:
                print(f"[ERR] âncora cap {ch} casou {len(hits)} vezes (precisa 1): {ANCHORS[ch]}", file=sys.stderr)
                return 1

        # inserir headings antes da âncora
        # estratégia: trabalhar por offsets (do fim pro começo)
        for ch in sorted(missing, reverse=True):
            rx = re.compile(ANCHORS[ch], re.MULTILINE)
            joined = "".join(text)
            m = rx.search(joined)
            assert m
            insert_at = m.start()

            # achar qual linha corresponde ao offset
            acc = 0
            idx = 0
            while idx < len(text) and acc + len(text[idx]) <= insert_at:
                acc += len(text[idx])
                idx += 1

            heading = f"## Chapter {ch}. {CANON_TITLES[ch-1]}\n\n"
            text.insert(idx, heading)

        # re-scan após injeção
        headings = [ln for ln in text if ln.startswith("## Chapter ")]
        got = sorted({int(re.match(r"^## Chapter (\d+)\.", ln).group(1)) for ln in headings if re.match(r"^## Chapter (\d+)\.", ln)})
        missing = [n for n in range(1, 23) if n not in got]
        if missing:
            print(f"[ERR] Ainda faltam capítulos após âncoras: {missing}", file=sys.stderr)
            return 1

    # 4) Garantir: sem duplicados
    nums = [int(re.match(r"^## Chapter (\d+)\.", ln).group(1)) for ln in headings if re.match(r"^## Chapter (\d+)\.", ln)]
    dups = sorted({n for n in nums if nums.count(n) > 1})
    if dups:
        print(f"[ERR] capítulos duplicados: {dups}", file=sys.stderr)
        return 1

    out.write_text("".join(text), encoding="utf-8")
    print("[OK] gerado:", out)
    print("[OK] backup:", backup)
    print("[OK] headings_count:", len(headings))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
