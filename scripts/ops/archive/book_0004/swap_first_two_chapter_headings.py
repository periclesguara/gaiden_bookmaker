from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines(True)  # keep \n
# A gente vai achar as duas PRIMEIRAS linhas que começam com "## Chapter "
idx = [i for i, ln in enumerate(lines) if ln.startswith("## Chapter ")]
if len(idx) < 2:
    print("[SKIP] Menos de 2 headings '## Chapter' encontrados.")
    sys.exit(0)

i1, i2 = idx[0], idx[1]

# Swap das linhas INTEIRAS
lines[i1], lines[i2] = lines[i2], lines[i1]

path.write_text("".join(lines), encoding="utf-8")
print(f"[OK] Swap linhas aplicado: line {i1+1} <-> line {i2+1}")
