from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines(True)  # keep newlines

# achar as duas primeiras headings
idx = [i for i, ln in enumerate(lines) if ln.startswith("## Chapter ")]
if len(idx) < 2:
    raise SystemExit("[ERR] Menos de 2 headings '## Chapter' encontrados.")

i1, i2 = idx[0], idx[1]

# Normalizar o par: Chapter 1 e Chapter 2 com títulos corretos
H1 = "## Chapter 1. O Sleeper, Awake!\n"
H2 = "## Chapter 2. The Hour of the Dragon\n"

# Se o primeiro heading do arquivo é Chapter 2, a gente escreve o H1 nele e H2 no segundo
# (não depende do texto antigo, é correção direta)
lines[i1] = H1
lines[i2] = H2

path.write_text("".join(lines), encoding="utf-8")
print(f"[OK] Top CH1/CH2 fix aplicado nas linhas {i1+1} e {i2+1}")
