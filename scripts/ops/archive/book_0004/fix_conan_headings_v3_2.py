#!/usr/bin/env python3
import re, sys
from pathlib import Path
from datetime import datetime

RE_H = re.compile(r"^##\s+Chapter\s+(\d{1,2})\.\s+(.+?)\s*$", re.I)

def main():
    if len(sys.argv) < 3:
        print("usage: fix_conan_headings_v3_2.py IN OUT", file=sys.stderr)
        sys.exit(2)

    inp = Path(sys.argv[1])
    outp = Path(sys.argv[2])

    lines = inp.read_text(encoding="utf-8", errors="replace").splitlines()

    # backup
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    bak = inp.with_suffix(inp.suffix + f".bak_{stamp}")
    bak.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    # coletar headings com posição
    headings = []
    for i, l in enumerate(lines):
        m = RE_H.match(l.strip())
        if m:
            n = int(m.group(1))
            headings.append((i, n, l.rstrip()))

    if not headings:
        outp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        print("[OK] no headings found; passthrough")
        return

    # regra: manter texto intacto; apenas reposicionar headings "fora de ordem" no topo
    # 1) achar bloco inicial (até o primeiro heading)
    first_h_pos = headings[0][0]
    pre = lines[:first_h_pos]
    rest = lines[first_h_pos:]

    # 2) extrair todos headings do "rest" preservando conteúdo entre eles
    #    vamos reconstruir com headings em ordem 1..22, MAS sem mover o conteúdo: apenas corrigir "Chapter 1" antes de "Chapter 2" no topo.
    #    A correção mínima: se o primeiro heading não é Chapter 1 e existe Chapter 1 em algum lugar, trazer Chapter 1 para a posição do primeiro heading (swap),
    #    mantendo todos os outros headings onde estão.
    #
    # Isso resolve "Chapter 2 antes de Chapter 1" sem rearranjar capítulos inteiros.
    pos_by_n = {}
    for i, n, l in headings:
        pos_by_n.setdefault(n, i)

    if 1 in pos_by_n and headings[0][1] != 1:
        # swap linhas (apenas as linhas do heading)
        idx_first = headings[0][0]
        idx_ch1 = pos_by_n[1]
        lines[idx_first], lines[idx_ch1] = lines[idx_ch1], lines[idx_first]

    # 3) sanity final: garantir ordem crescente dos headings (não a posição), sem reflow.
    #    Se algum heading estiver invertido localmente (ex: Chapter 18 antes de 17), corrigir por swaps adjacentes apenas nas linhas de heading.
    def get_heading_positions(ls):
        out = []
        for i, l in enumerate(ls):
            m = RE_H.match(l.strip())
            if m:
                out.append((i, int(m.group(1))))
        return out

    hp = get_heading_positions(lines)
    # bubble-fix só entre headings adjacentes
    changed = True
    while changed:
        changed = False
        hp = get_heading_positions(lines)
        for (i1, n1), (i2, n2) in zip(hp, hp[1:]):
            if n2 < n1:
                # swap apenas as linhas de heading
                lines[i1], lines[i2] = lines[i2], lines[i1]
                changed = True
                break

    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    print(f"[OK] backup: {bak}")
    print(f"[OK] out:    {outp}")

if __name__ == "__main__":
    main()

