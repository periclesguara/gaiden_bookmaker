#!/usr/bin/env python3
import re, sys
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
CANON_BY_N = {n:t for n,t in CANON}

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

def norm(s: str) -> str:
    s = s.strip()
    # normaliza aspas e apóstrofos
    s = s.replace("’","'").replace("“",'"').replace("”",'"')
    s = re.sub(r"\s+", " ", s)
    s = s.lower()
    # remove pontuação “não-semântica” pra casar variantes (Awake vs Wake Up! etc)
    s = re.sub(r"[^\w\s']", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def chapter_heading(n: int) -> str:
    return f"## Chapter {n}. {CANON_BY_N[n]}"

# 1) título -> capítulo, com variantes
TITLE_IDX = {}
for n, title in CANON:
    TITLE_IDX[norm(title)] = n
    # variantes comuns de Cap 1
    if n == 1:
        for v in [
            "O Sleeper, Wake Up!",
            "O Sleeper, Wake Up",
            "O Sleeper—Wake Up!",
            "O Sleeper, Awake",
            "O Sleeper—Awake!",
            "O Sleeper, Awake!",
        ]:
            TITLE_IDX[norm(v)] = 1
    # variantes sem aspas
    TITLE_IDX[norm(title.replace("“","").replace("”",""))] = n

# padrões
RE_NUM_PHRASE = re.compile(r"^\s*(\d{1,2})\s*[-–—]\s*(.+?)\s*$")
RE_CHAPTER = re.compile(r"^\s*(chapter)\s+(\d{1,2}|[ivxlcdm]+)\b[.\s\-–—:]*\s*(.*?)\s*$", re.I)

def is_title_line(line: str):
    return TITLE_IDX.get(norm(line))

def main():
    if len(sys.argv) < 3:
        print("usage: fix_conan_headings_v3_1.py IN OUT", file=sys.stderr)
        sys.exit(2)

    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    raw = in_path.read_text(encoding="utf-8", errors="replace").splitlines()

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup = in_path.with_suffix(in_path.suffix + f".bak_{stamp}")
    backup.write_text("\n".join(raw) + "\n", encoding="utf-8")

    out = []
    seen = set()

    i = 0
    while i < len(raw):
        line = raw[i].rstrip()
        nxt  = raw[i+1].rstrip() if i+1 < len(raw) else ""

        # A) "9 – TITLE" (num-frase)
        m = RE_NUM_PHRASE.match(line)
        if m:
            n = int(m.group(1))
            rest = m.group(2).strip()
            n_title = is_title_line(rest)  # casa por título (variante tolerante)
            if n_title and n_title not in seen:
                out.append(chapter_heading(n_title))
                seen.add(n_title)
                # pula duplicata em linha seguinte (caps/titlecase)
                if is_title_line(nxt):
                    i += 2
                    continue
                i += 1
                continue
            # se não casa com título canônico: remove ruído
            i += 1
            continue

        # B) "CHAPTER V" / "Chapter 10"
        m2 = RE_CHAPTER.match(line)
        if m2:
            n = to_int(m2.group(2))
            tail = (m2.group(3) or "").strip()
            # tenta casar pelo tail, senão usa o número
            n_by_tail = is_title_line(tail) if tail else None
            n_final = n_by_tail or n
            if n_final and 1 <= n_final <= 22:
                if n_final not in seen:
                    out.append(chapter_heading(n_final))
                    seen.add(n_final)
                # remove duplicata de título imediatamente abaixo
                if is_title_line(nxt):
                    i += 2
                    continue
                i += 1
                continue
            i += 1
            continue

        # C) linha que é só título (caps/titlecase)
        n3 = is_title_line(line)
        if n3:
            if n3 not in seen:
                out.append(chapter_heading(n3))
                seen.add(n3)
            # remove duplicata imediata
            if is_title_line(nxt):
                i += 2
                continue
            i += 1
            continue

        out.append(line)
        i += 1

    # Fallback determinístico: garantir 22 headings
    # Estratégia: onde houver lacunas (ex. 4 -> 6), inserir headings faltantes antes do próximo heading.
    fixed = []
    chapter_re = re.compile(r"^## Chapter (\d{1,2})\.", re.I)
    existing_positions = []
    for idx, l in enumerate(out):
        m = chapter_re.match(l.strip())
        if m:
            existing_positions.append((idx, int(m.group(1))))

    if existing_positions:
        # constrói um set atual de capítulos
        present = {n for _, n in existing_positions}
        # percorre a lista e preenche gaps
        pos_map = {pos:n for pos,n in existing_positions}
        idx = 0
        while idx < len(out):
            if idx in pos_map:
                cur = pos_map[idx]
                # olha o próximo heading
                next_heading = None
                for j in range(idx+1, len(out)):
                    if j in pos_map:
                        next_heading = pos_map[j]
                        next_pos = j
                        break
                fixed.append(out[idx])
                # se tem próximo e há lacuna, insere headings faltantes logo após o atual
                if next_heading and next_heading > cur + 1:
                    for missing in range(cur+1, next_heading):
                        if 1 <= missing <= 22 and missing not in present:
                            fixed.append(chapter_heading(missing))
                            present.add(missing)
                idx += 1
                continue
            fixed.append(out[idx])
            idx += 1

        # se ainda faltar algum (ex: início/fim), adiciona no começo ou no final (raríssimo, mas garante)
        present = {int(chapter_re.match(l).group(1)) for l in fixed if chapter_re.match(l.strip())}
        missing_all = [n for n in range(1,23) if n not in present]
        if missing_all:
            # se faltar no início, põe no começo; se faltar no fim, põe no fim
            for n in missing_all:
                if n < min(present):
                    fixed.insert(0, chapter_heading(n))
                else:
                    fixed.append(chapter_heading(n))

        out = fixed

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")

    print(f"[OK] backup: {backup}")
    print(f"[OK] out:    {out_path}")

if __name__ == "__main__":
    main()
