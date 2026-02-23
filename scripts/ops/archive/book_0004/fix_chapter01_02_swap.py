from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

lines = text.splitlines(True)  # keep \n
# Vamos olhar só o topo, porque o problema está no início
top_n = min(len(lines), 80)
top = "".join(lines[:top_n])

# Captura heading "## Chapter X ..." (preserva título)
pat = re.compile(r"^##\s+Chapter\s+([0-9]{1,2})\b.*?$", re.MULTILINE)

# Localiza as duas primeiras ocorrências de Chapter 1 e Chapter 2 no topo
m1 = None
m2 = None
for m in pat.finditer(top):
    num = int(m.group(1))
    if num == 1 and m1 is None:
        m1 = m
    if num == 2 and m2 is None:
        m2 = m
    if m1 and m2:
        break

if not (m1 and m2):
    print("[SKIP] Não encontrei Chapter 1 e Chapter 2 no topo para swap.")
    sys.exit(0)

# Se Chapter 2 aparece antes de Chapter 1, swap apenas os números, mantendo o texto do título
if m2.start() < m1.start():
    # Troca só a parte "Chapter N" na linha inteira, sem mexer no resto
    def swap_line(s: str) -> str:
        s = re.sub(r"^(##\s+Chapter\s+)2(\b)", r"\g<1>__TMP__\2", s)
        s = re.sub(r"^(##\s+Chapter\s+)1(\b)", r"\g<1>2\2", s)
        s = re.sub(r"^(##\s+Chapter\s+)__TMP__(\b)", r"\g<1>1\2", s)
        return s

    # Aplica apenas nas linhas dos matches (offset por linha)
    # Reconstrói linhas e troca somente as duas linhas alvo
    i1 = top[:m1.start()].count("\n")
    i2 = top[:m2.start()].count("\n")

    # swap nesses índices
    lines[i1] = swap_line(lines[i1])
    lines[i2] = swap_line(lines[i2])

    out = "".join(lines)
    path.write_text(out, encoding="utf-8")
    print("[OK] Swap Chapter 1/2 aplicado no topo.")
else:
    print("[OK] Ordem já está Chapter 1 antes de Chapter 2. Nada a fazer.")
