#!/usr/bin/env python3
import re
from pathlib import Path

# Caminho do merge EN polido (ja existente)
INPUT = Path("data/chunks/book_0001/refine_en_01/merged_en_modern_2025.txt")

# Novo arquivo normalizado (nao sobrescreve o original)
OUTPUT = Path("data/chunks/book_0001/refine_en_01/merged_en_modern_2025_normalized.txt")

ROMAN_MAP = {
    "I": 1, "II": 2, "III": 3, "IV": 4,
    "V": 5, "VI": 6, "VII": 7, "VIII": 8,
    "IX": 9, "X": 10, "XI": 11, "XII": 12,
}

TITLE_MAP = {
    1: "A Scandal in Bohemia",
    2: "A Case of Identity",
    3: "The Red-Headed League",
    4: "The Boscombe Valley Mystery",
    5: "The Five Orange Pips",
    6: "The Man with the Twisted Lip",
    7: "The Adventure of the Blue Carbuncle",
    8: "The Adventure of the Speckled Band",
    9: "The Adventure of the Engineer's Thumb",
    10: "The Adventure of the Noble Bachelor",
    11: "The Adventure of the Beryl Coronet",
    12: "The Adventure of the Copper Beeches",
}

ROMAN_LINE_RE = re.compile(r"^\s*([IVXLCDM]+)\s*$")

# Marca onde comeca o texto narrativo pra cortar indice/capa velha
FIRST_STORY_START = "To Sherlock Holmes, she is always"


def main() -> None:
    if not INPUT.exists():
        raise SystemExit(f"Input nao encontrado: {INPUT}")

    raw = INPUT.read_text(encoding="utf-8")
    lines = raw.splitlines()

    output_lines: list[str] = []

    started_narrative = False
    current_chapter: int | None = None
    sub_idx = 0

    for line in lines:
        stripped = line.strip()

        # 1) Cortar indice / lixo antes da primeira frase do conto 1
        if not started_narrative:
            if FIRST_STORY_START in line:
                current_chapter = 1
                sub_idx = 0

                # Titulo centralizado do conto 1
                output_lines.append(":: center")
                output_lines.append(f"# I. {TITLE_MAP[1]}")
                output_lines.append(":::")
                output_lines.append("")

                started_narrative = True
                output_lines.append(line)
            # Ignora tudo antes
            continue

        # 2) Dentro da narrativa: olhar linhas so com numero romano
        m = ROMAN_LINE_RE.match(stripped)
        if m:
            roman = m.group(1)
            num = ROMAN_MAP.get(roman)

            if num is None:
                output_lines.append(line)
                continue

            # 2.1) Se e um romano que corresponde a um conto e mudou de capitulo
            if num in TITLE_MAP and (current_chapter != num):
                current_chapter = num
                sub_idx = 0

                output_lines.append("")
                output_lines.append(":: center")
                output_lines.append(f"# {roman}. {TITLE_MAP[num]}")
                output_lines.append(":::")
                output_lines.append("")
                continue

            # 2.2) Caso contrario: trata como subcapitulo interno -> numero arabico
            if current_chapter is not None:
                sub_idx += 1
                output_lines.append("")
                output_lines.append(f"## {sub_idx}")
                output_lines.append("")
                continue

            # fallback
            output_lines.append(line)
            continue

        # 3) Linha normal: copia
        output_lines.append(line)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(output_lines), encoding="utf-8")
    print(f"OK: escrito em {OUTPUT}")


if __name__ == "__main__":
    main()
