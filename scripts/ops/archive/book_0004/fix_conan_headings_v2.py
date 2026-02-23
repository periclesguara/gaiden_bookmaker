import re
from pathlib import Path

# Regras:
# - Remove linhas "NUM – FRASE" (ex.: 9 – “IT IS THE KING OR HIS GHOST!”)
# - Se a linha seguinte for o título "limpo" (Title Case) e bater com a frase (case-insensitive),
#   promove esse título para heading MD: "## Chapter N — <Title>"
# - Se NÃO houver linha seguinte limpa, usa a própria frase (sem o número) como título (heading).
# - Também promove "O Sleeper, Awake!" como Chapter 1 caso exista como linha solta.
#
# Observação:
# Isso NÃO mexe em prosa, não reordena parágrafos, só limpa artefato e cria headings.

NUM_TITLE_RE = re.compile(r'^\s*(\d+)\s*[\-–—]\s*(.+?)\s*$')
MD_HEADING_RE = re.compile(r'^\s*##\s+Chapter\s+\d+\b', re.IGNORECASE)

def norm_title(s: str) -> str:
    s = s.strip()
    # normalização leve: aspas e múltiplos espaços
    s = s.replace('“','"').replace('”','"').replace("’","'").replace("–","-").replace("—","-")
    s = re.sub(r'\s+', ' ', s)
    return s.strip()

def looks_like_title_case(s: str) -> bool:
    s = s.strip()
    if not s or len(s) > 120: return False
    if re.search(r'[.?!]$', s): return False
    # pelo menos 2 palavras ou contém vírgula/aspas (caso do "O Sleeper, Awake!")
    if len(s.split()) < 2 and ',' not in s and '"' not in s: return False
    # muitos tokens com inicial maiúscula
    words = [w for w in re.split(r'\s+', s) if w]
    caps = sum(1 for w in words if w[:1].isupper())
    return caps / max(1, len(words)) >= 0.60 or s.isupper()

def main():
    import sys
    if len(sys.argv) != 3:
        print("usage: fix_conan_headings_v2.py IN OUT")
        sys.exit(2)

    inp = Path(sys.argv[1])
    out = Path(sys.argv[2])
    lines = inp.read_text(encoding="utf-8", errors="replace").splitlines()

    res = []
    i = 0

    # Passo A: varrer e transformar pares NUM–FRASE + TitleCase
    while i < len(lines):
        line = lines[i]
        m = NUM_TITLE_RE.match(line)
        if m:
            chap_n = m.group(1)
            raw_title = norm_title(m.group(2))
            nxt = lines[i+1] if i+1 < len(lines) else ""
            nxt_norm = norm_title(nxt)

            # Caso clássico: linha seguinte é o título “limpo” do capítulo
            if nxt.strip() and looks_like_title_case(nxt_norm):
                # Se bate "por conteúdo" (muito parecido), usa o nxt como título.
                # (aqui: checagem relaxada, só exige que alguma palavra forte apareça)
                raw_words = set(w.lower() for w in re.findall(r"[A-Za-z']+", raw_title) if len(w) >= 4)
                nxt_words = set(w.lower() for w in re.findall(r"[A-Za-z']+", nxt_norm) if len(w) >= 4)
                overlap = len(raw_words & nxt_words)

                if overlap >= 1:
                    # remove a linha NUM–FRASE e também remove o título duplicado,
                    # substituindo por heading MD
                    res.append(f"## Chapter {chap_n} — {nxt_norm}")
                    res.append("")  # espaço pós-heading
                    i += 2
                    continue

            # Caso não tem “duplicata limpa”: vira heading com a própria frase
            res.append(f"## Chapter {chap_n} — {raw_title}")
            res.append("")
            i += 1
            continue

        res.append(line)
        i += 1

    # Passo B: promover Chapter 1 (O Sleeper, Awake!/Wake Up!) se estiver solto
    # Critério: primeira ocorrência dessas variantes que NÃO esteja logo após um heading
    fixed = []
    inserted_ch1 = False
    for j, line in enumerate(res):
        low = norm_title(line).lower()
        if (not inserted_ch1) and (
            low == "o sleeper, awake!" or low == "o sleeper, wake up!"
        ):
            prev = fixed[-1] if fixed else ""
            if not MD_HEADING_RE.match(prev):
                fixed.append("## Chapter 1 — O Sleeper, Wake Up!")
                fixed.append("")
                inserted_ch1 = True
                # mantém a linha original como título? NÃO: vira duplicata. Então pulamos ela.
                continue
        fixed.append(line)

    # Passo C: normalização mínima: no máximo 2 linhas em branco seguidas
    final = []
    blank_run = 0
    for line in fixed:
        if line.strip() == "":
            blank_run += 1
            if blank_run <= 2:
                final.append("")
        else:
            blank_run = 0
            final.append(line.rstrip())

    out.write_text("\n".join(final).rstrip() + "\n", encoding="utf-8")
    print(f"[OK] wrote: {out}")

if __name__ == "__main__":
    main()
