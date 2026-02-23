from pathlib import Path
import re
import sys

inp = Path(sys.argv[1])
out = Path(sys.argv[2])

lines = inp.read_text(encoding="utf-8").splitlines(True)

# Detect an already-canonical chapter heading
canon = re.compile(r"^##\s+Chapter\s+(\d{1,2})\.\s+.+\s*$")

# Official title lines (numeric + dash + title), examples:
# 9 – “IT IS THE KING OR HIS GHOST!”
# 12 - THE FANG OF THE DRAGON
# 6 - THE THRUST OF A KNIFE
# 9.2 - A BLACK WIND BLOWS   (we ignore decimals as "not a chapter number")
official = re.compile(r"^\s*(\d{1,2})\s*[-–—]\s*(.+?)\s*$")

# Old headings that must die: typically ALLCAPS headings that are NOT "## Chapter N."
# We'll remove ONLY headings lines (##... or ###...) that are mostly uppercase.
heading = re.compile(r"^(#{2,6})\s+(.+?)\s*$")
mostly_caps = re.compile(r"^[^a-z]*[A-Z][^a-z]*$")  # no lowercase letters

out_lines = []
seen_chapters = set()

def norm_title(t: str) -> str:
    t = t.strip()
    # strip wrapping quotes if present, keep inner punctuation
    # but DO NOT alter names/terms; just remove surrounding quotes characters
    t = t.strip(' \t\r\n"“”')
    return t

for ln in lines:
    # Track existing canonical headings
    mcanon = canon.match(ln)
    if mcanon:
        seen_chapters.add(int(mcanon.group(1)))
        out_lines.append(ln)
        continue

    # Remove old ALLCAPS headings (wrong titles)
    mh = heading.match(ln)
    if mh:
        txt = mh.group(2).strip()
        # if it's already a canonical Chapter line, handled above
        # remove only if it looks like old allcaps/no-lowercase
        if mostly_caps.match(txt) and not txt.lower().startswith("chapter "):
            # drop it
            continue
        # keep other headings (rare)
        out_lines.append(ln)
        continue

    # Promote official numeric-title lines to canonical chapter headings
    mo = official.match(ln)
    if mo:
        n = int(mo.group(1))
        title = norm_title(mo.group(2))

        # ignore decimals like 9.2 because regex doesn't capture them; ok.
        if 1 <= n <= 22:
            if n not in seen_chapters:
                out_lines.append(f"## Chapter {n}. {title}\n")
                seen_chapters.add(n)
            # remove the original numeric line (we replaced it)
            continue

    out_lines.append(ln)

out.write_text("".join(out_lines), encoding="utf-8")

# SANITY
txt = out.read_text(encoding="utf-8")
chap = list(map(int, re.findall(r"^##\s+Chapter\s+(\d{1,2})\.", txt, flags=re.M)))
dups = sorted({n for n in chap if chap.count(n) > 1})
missing = [n for n in range(1, 23) if n not in chap]

print("[OK] wrote:", out)
print("[SANITY] headings_count:", len(chap))
print("[SANITY] dup_chapters:", dups)
print("[SANITY] missing_chapters:", missing)

# show first 30 chapter headings
print("== first chapter headings ==")
for m in re.finditer(r"^##\s+Chapter\s+\d{1,2}\..*$", txt, flags=re.M):
    print(m.group(0))
    if m.start() > 0 and txt.count("\n", 0, m.start()) > 300:  # stop after some
        break
