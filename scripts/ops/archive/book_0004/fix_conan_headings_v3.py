#!/usr/bin/env python3
import re
import sys
from pathlib import Path
from datetime import datetime

CANON = [
    (1,  "O Sleeper, Awake!"),
    (2,  "The Hour of the Dragon"),
    (3,  "The Cliffs Reel"),
    (4,  "“What Hell Did You Crawl Out Of?”"),
    (5,  "The Devil’s Bargain"),
    (6,  "The Thrust of a Knife"),
    (7,  "The Rending of the Veil"),
    (8,  "Dying Embers"),
    (9,  "“It Is the King or his Ghost!”"),
    (10, "A Coin From Acheron"),
    (11, "Swords of the South"),
    (12, "The Fang of the Dragon"),
    (13, "“A Ghost Out of the Past”"),
    (14, "The Black Hand of Set"),
    (15, "The Return of the Corsair"),
    (16, "Black-Walled Khemi"),
    (17, "“He Has Slain the Sacred Son of Set!”"),
    (18, "“I Am the Woman Who Never Died”"),
    (19, "In the Hall of the Dead"),
    (20, "Out of the Dust Shall Acheron Arise"),
    (21, "Drums of Peril"),
    (22, "The Road to Acheron"),
]

def norm(s: str) -> str:
    s = s.strip()
    s = s.replace("’","'").replace("“",'"').replace("”",'"')
    s = re.sub(r"\s+", " ", s)
    s = s.lower()
    return s

# padrões comuns de "isca" e duplicação
RE_NUM_PHRASE = re.compile(r"^\s*(\d{1,2})\s*[-–—]\s*(.+?)\s*$")
RE_CHAPTER_LINE = re.compile(r"^\s*(chapter|CHAPTER)\s+(\d{1,2}|[ivxlcdm]+)\b[.\s–—:\-]*\s*(.*?)\s*$", re.I)

ROMAN = {
    "i":1,"ii":2,"iii":3,"iv":4,"v":5,"vi":6,"vii":7,"viii":8,"ix":9,"x":10,
    "xi":11,"xii":12,"xiii":13,"xiv":14,"xv":15,"xvi":16,"xvii":17,"xviii":18,"xix":19,"xx":20,
    "xxi":21,"xxii":22
}

def to_int(tok: str):
    t = tok.strip().lower()
    if t.isdigit():
        return int(t)
    return ROMAN.get(t)

def build_title_index():
    # mapeia variantes plausíveis -> capítulo
    idx = {}
    for n, title in CANON:
        t0 = norm(title)
        idx[t0] = (n, title)

        # variações sem aspas / com aspas
        t_plain = norm(re.sub(r'^"|"$', '', t0))
        idx[t_plain] = (n, title)

        # variação ALL CAPS
        idx[norm(title.upper())] = (n, title)

        # variação com aspas " normais "
        title_quotes = title.replace("“", '"').replace("”", '"')
        idx[norm(title_quotes)] = (n, title)
    return idx

TITLE_IDX = build_title_index()

def is_probable_title_line(line: str):
    t = norm(line)
    return t in TITLE_IDX

def chapter_heading(n: int, title: str) -> str:
    # padrão final único
    return f"## Chapter {n}. {title}"

def main():
    if len(sys.argv) < 3:
        print("usage: fix_conan_headings_v3.py IN OUT", file=sys.stderr)
        sys.exit(2)

    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    raw = in_path.read_text(encoding="utf-8", errors="replace").splitlines()

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup = in_path.with_suffix(in_path.suffix + f".bak_{stamp}")
    backup.write_text("\n".join(raw) + "\n", encoding="utf-8")

    out = []
    seen_chapters = set()
    i = 0

    while i < len(raw):
        line = raw[i].rstrip()
        nxt = raw[i+1].rstrip() if i+1 < len(raw) else ""

        # 1) detectar linhas tipo "9 – “IT IS ...”" ou "12 - THE FANG ..."
        m = RE_NUM_PHRASE.match(line)
        if m:
            n = int(m.group(1))
            rest = m.group(2).strip()

            # se o "rest" é um título canônico (ou variante), vira heading MD e evita duplicar
            key = norm(rest)
            if key in TITLE_IDX:
                n2, title = TITLE_IDX[key]
                if 1 <= n2 <= 22 and n2 not in seen_chapters:
                    out.append(chapter_heading(n2, title))
                    seen_chapters.add(n2)

                    # se a próxima linha for o mesmo título (duplicado), pula
                    if is_probable_title_line(nxt):
                        i += 2
                        continue

                    i += 1
                    continue

            # se não casa com título canônico, é ruído: remove (não imprime)
            i += 1
            continue

        # 2) detectar linhas "CHAPTER X" / "Chapter 12" etc
        m2 = RE_CHAPTER_LINE.match(line)
        if m2:
            n = to_int(m2.group(2))
            tail = m2.group(3).strip()
            tail_key = norm(tail) if tail else ""

            # escolhe o título canônico pelo tail se existir, senão pelo número
            title = None
            if tail and tail_key in TITLE_IDX:
                n2, tcanon = TITLE_IDX[tail_key]
                n = n2
                title = tcanon
            elif n and 1 <= n <= 22:
                title = dict(CANON).get(n)

            if n and title and n not in seen_chapters:
                out.append(chapter_heading(n, title))
                seen_chapters.add(n)

                # pular duplicação: se próxima linha repetir o título em caps/titlecase, remove
                if is_probable_title_line(nxt):
                    i += 2
                    continue

                i += 1
                continue

            # se já viu, remove duplicata
            i += 1
            continue

        # 3) se linha é só o título (caps ou title case), transforma em heading MD
        if is_probable_title_line(line):
            n, title = TITLE_IDX[norm(line)]
            if n not in seen_chapters:
                out.append(chapter_heading(n, title))
                seen_chapters.add(n)

                # se próxima linha também for título duplicado, pula
                if is_probable_title_line(nxt):
                    i += 2
                    continue

                i += 1
                continue
            else:
                # duplicata: remove
                i += 1
                continue

        # 4) limpeza leve de espaçamento (sem mexer no texto)
        out.append(line)
        i += 1

    # sanity: garantir que não houve headings duplicados
    # (não força completar faltantes aqui, apenas não duplica)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")

    print(f"[OK] backup: {backup}")
    print(f"[OK] out:    {out_path}")

if __name__ == "__main__":
    main()
