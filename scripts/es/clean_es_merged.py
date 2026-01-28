import os, re, sys
from pathlib import Path

merged = os.environ.get("MERGED")
clean = os.environ.get("CLEAN")

if not merged or not clean:
    print("ERROR: env MERGED and CLEAN must be set.", file=sys.stderr)
    sys.exit(2)

src = Path(merged)
dst = Path(clean)

if not src.exists():
    print(f"ERROR: MERGED file not found: {src}", file=sys.stderr)
    sys.exit(3)

text = src.read_text(encoding="utf-8", errors="replace")

# limpeza conservadora
text = text.replace("\r\n", "\n").replace("\r", "\n")           # normaliza EOL
text = re.sub(r"[ \t]+\n", "\n", text)                          # remove trailing spaces
text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"           # colapsa múltiplas linhas vazias

dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_text(text, encoding="utf-8")

print(f"OK: wrote {dst} ({dst.stat().st_size} bytes)")
