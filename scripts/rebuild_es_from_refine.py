#!/usr/bin/env python3
# scripts/rebuild_es_from_refine.py
# TXT canônico (merge_refine.txt) -> MD com headings + pagebreaks + frontmatter -> EPUB com TOC/cover/metadata.
# Objetivo: ZERO gambiarra manual.

from __future__ import annotations
import re
import unicodedata
from pathlib import Path
import subprocess
import sys
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]

# Ajuste aqui se seu layout for diferente
BUILD_DIR = ROOT / "data" / "builds" / "book_0001" / "es"
FRONT_DIR = ROOT / "data" / "frontmatter" / "book_0001" / "es"
RELEASES = ROOT / "releases"

SRC_TXT = BUILD_DIR / "merge_refine.txt"
OUT_MIOL_MD = BUILD_DIR / "MIOL_ES.from_refine.md"
OUT_BUILD_MD = BUILD_DIR / "BOOK.BUILD.MD"
CSS_FILE = BUILD_DIR / "BOOK.epub.css"
COVER_JPG = ROOT / "data" / "builds" / "book01_the_adventures_of_sherlock_holmes" / "es" / "Cover_Sherlock_Holmes_ES.jpg"

OUT_EPUB = BUILD_DIR / "BOOK.epub"

BOOK_TITLE = "Las Aventuras de Sherlock Holmes"
BOOK_SUBTITLE = "Edición en Español Moderno"
AUTHOR = "Arthur Conan Doyle"
ADAPTER = "Hans Hermann Ironside"
IMPRINT = "MantaQuest"
CITY = "Río de Janeiro"
COUNTRY = "Brasil"
YEAR = "2026"
LANG = "es"

# Lista oficial (12 contos) – serve como fallback e também como validação.
STORIES_ES = [
    "UN ESCÁNDALO EN BOHEMIA",
    "LA LIGA DE LOS PELIRROJOS",
    "UN CASO DE IDENTIDAD",
    "EL MISTERIO DEL VALLE DE BOSCOMBE",
    "LAS CINCO SEMILLAS DE NARANJA",
    "EL HOMBRE DEL LABIO TORCIDO",
    "LA AVENTURA DEL CARBUNCO AZUL",
    "LA AVENTURA DE LA BANDA DE LUNARES",
    "LA AVENTURA DEL PULGAR DEL INGENIERO",
    "LA AVENTURA DEL NOBLE SOLTERO",
    "LA AVENTURA DE LA CORONA DE BERILOS",
    "LA AVENTURA DE LAS HAYAS COBRIZAS",
]


def norm(s: str) -> str:
    s = s.strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.upper()
    s = re.sub(r"\s+", " ", s)
    return s


def pagebreak_html() -> str:
    # Pandoc respeita isso no EPUB (Kindle Previewer também costuma respeitar).
    return '<div style="page-break-before: always;"></div>\n'


def read_text(p: Path) -> str:
    if not p.exists():
        raise FileNotFoundError(f"Fonte não encontrada: {p}")
    return p.read_text(encoding="utf-8", errors="replace")


def load_frontmatter() -> dict[str, str]:
    # Regra: entra só se tiver conteúdo e SEM carimbo/sem pagebreak “texto”
    fm = {}
    mapping = {
        "frontispiece": FRONT_DIR / "frontispiece.md",
        "copyright": FRONT_DIR / "copyright.md",
        "about_edition": FRONT_DIR / "about_edition.md",
        "about_contributor": FRONT_DIR / "about_contributor.md",
        "introduction": FRONT_DIR / "introduction.md",
        "epilogue": FRONT_DIR / "epilogue.md",
    }
    for k, f in mapping.items():
        if f.exists():
            txt = f.read_text(encoding="utf-8", errors="replace").strip()
            # sanitização: remove carimbo acidental no conteúdo
            txt = re.sub(r"__FM_ES__\d{4}-\d{2}-\d{2}.*", "", txt).strip()
            # remove “::: pagebreak” que vira texto
            txt = re.sub(r"^:::\s*pagebreak\s*$", "", txt, flags=re.M).strip()
            fm[k] = txt
        else:
            fm[k] = ""
    return fm


def _is_title_like(title: str) -> bool:
    # Heuristic: mostly uppercase, short, and not too many lowercase letters.
    if len(title) > 120:
        return False
    lower = sum(1 for ch in title if ch.islower())
    return lower <= 2


def _split_title_remainder(text: str) -> tuple[str, str]:
    # Split on common separators between title and narrative.
    parts = re.split(r"\s*—\s*|\s+-\s+|\s+–\s+", text, maxsplit=1)
    title = parts[0].strip()
    remainder = parts[1].strip() if len(parts) > 1 else ""
    return title, remainder


def detect_numbered_headings(lines: list[str]) -> list[tuple[int, int, str, str]]:
    """
    Retorna lista de (line_idx, n, title)
    Captura títulos no padrão:
      1. UN ESCÁNDALO EN BOHEMIA
      10. LA AVENTURA ...
    E também:
      1) ...
      1 - ...
    """
    hits: list[tuple[int, int, str, str]] = []
    rx = re.compile(r"^\s*(\d{1,2})\s*[\.\)\-]\s*(.+?)\s*$")
    for i, ln in enumerate(lines):
        m = rx.match(ln)
        if not m:
            continue
        n = int(m.group(1))
        raw_title = m.group(2).strip()
        title, remainder = _split_title_remainder(raw_title)
        # heurística: títulos tendem a ser curtos e em maiúsculas
        if 1 <= n <= 12 and _is_title_like(title):
            # aceitar mesmo se tiver acentos; normalizar só para comparar
            hits.append((i, n, title, remainder))
    # remover duplicatas por número (ficar com a primeira ocorrência)
    out: list[tuple[int, int, str, str]] = []
    seen = set()
    for i, n, t, r in hits:
        if n not in seen:
            out.append((i, n, t, r))
            seen.add(n)
    return out


def insert_story_headings(text: str) -> tuple[str, list[str]]:
    """
    Tenta:
    A) detectar 1..12 via regex de numbering
    B) se não der 12, faz fallback procurando os títulos conhecidos (STORIES_ES)
    Retorna (md_text, story_titles_used)
    """
    # Preprocess: force line breaks before inline chapter markers like " ... 3. TÍTULO"
    text = re.sub(
        r"(?<!\n)\s+(\d{1,2}\.\s+[A-ZÁÉÍÓÚÑÜ])",
        r"\n\1",
        text,
    )
    lines = text.splitlines()
    numbered = detect_numbered_headings(lines)

    # Se achou 12, ótimo.
    if len(numbered) == 12:
        story_titles = []
        out = []
        for idx, (i, n, title, remainder) in enumerate(numbered):
            story_titles.append(f"{n}. {title.strip()}")
        # reconstrói com headings e pagebreak antes de cada história (exceto a primeira)
        mark = {i: (n, title, remainder) for i, n, title, remainder in numbered}

        first = True
        for i, ln in enumerate(lines):
            if i in mark:
                n, title, remainder = mark[i]
                if not first:
                    out.append(pagebreak_html().rstrip("\n"))
                first = False
                out.append(f"## {n}. {title.strip()}")
                out.append("")  # linha em branco
                if remainder:
                    out.append(remainder)
                continue
            out.append(ln)
        return "\n".join(out).strip() + "\n", story_titles

    # Fallback: procurar títulos conhecidos
    story_titles = []
    out_lines = []
    # cria um “set” de títulos normalizados para match linha-a-linha
    wanted = {norm(t): t for t in STORIES_ES}
    # também aceita “N. TÍTULO” caso o texto tenha número embutido
    rx_anynum = re.compile(r"^\s*(\d{1,2})\s*[\.\)\-]\s*(.+?)\s*$")

    found_norm = set()

    for ln in lines:
        raw = ln.strip()
        m = rx_anynum.match(raw)
        candidate_title = None
        candidate_num = None

        if m:
            candidate_num = int(m.group(1))
            candidate_title = m.group(2).strip()

        # match por linha “título puro” ou “n. título”
        key1 = norm(raw)
        key2 = norm(candidate_title) if candidate_title else ""

        matched = None
        matched_num = None

        if key1 in wanted:
            matched = wanted[key1]
        elif key2 in wanted and candidate_num and 1 <= candidate_num <= 12:
            matched = wanted[key2]
            matched_num = candidate_num

        if matched and norm(matched) not in found_norm:
            # quebra antes de cada história (exceto se for a primeira encontrada)
            if found_norm:
                out_lines.append(pagebreak_html().rstrip("\n"))
            found_norm.add(norm(matched))

            # numeração: se veio do texto, usa; senão usa ordem oficial
            if matched_num is None:
                matched_num = STORIES_ES.index(matched) + 1

            story_titles.append(f"{matched_num}. {matched}")
            out_lines.append(f"## {matched_num}. {matched}")
            out_lines.append("")
            # se a linha original era “n. título”, não repete
            continue

        out_lines.append(ln)

    # validação final
    if len(story_titles) != 12:
        raise RuntimeError(
            f"Falha na segmentação: detectou {len(story_titles)} histórias (esperado 12). "
            "Ou o TXT está com títulos diferentes, ou há ruído/variação. "
            "Ajuste STORIES_ES ou refine o match."
        )

    return "\n".join(out_lines).strip() + "\n", story_titles


def build_css():
    # CSS minimalista pra centrar títulos e deixar “páginas” limpas
    CSS_FILE.write_text(
        """
/* Centralização de títulos */
h1, h2, h3 { text-align: center; }

/* Mais respiro */
body { line-height: 1.35; }

/* Evitar quebras feias logo após heading */
h2 { page-break-after: avoid; }

/* Garantir que o div de pagebreak funcione */
div[style*="page-break-before"] { page-break-before: always; }
""".lstrip(),
        encoding="utf-8",
    )


def write_build_md(miolo_md: str):
    fm = load_frontmatter()

    # YAML metadata: isso ajuda MUITO o OPF (dc:title etc.)
    yaml = f"""---
title: "{BOOK_TITLE} ({BOOK_SUBTITLE})"
subtitle: "{BOOK_SUBTITLE}"
author: "{AUTHOR}"
lang: "{LANG}"
publisher: "RinoBooks"
rights: "Dominio público en los Estados Unidos y otros territorios."
...
"""

    parts = [yaml]

    def add_section(txt: str):
        txt = (txt or "").strip()
        if not txt:
            return
        parts.append(txt)
        parts.append("")  # newline
        parts.append(pagebreak_html().rstrip("\n"))
        parts.append("")

    # Ordem recomendada (sem índice manual no frontmatter):
    # frontispiece -> copyright -> about_edition -> (miolo)
    add_section(fm.get("frontispiece", ""))
    add_section(fm.get("copyright", ""))
    add_section(fm.get("about_edition", ""))
    # opcional
    add_section(fm.get("about_contributor", ""))
    add_section(fm.get("introduction", ""))

    parts.append(miolo_md.strip())
    parts.append("")
    # opcional epílogo (quebra antes)
    ep = (fm.get("epilogue", "") or "").strip()
    if ep:
        parts.append(pagebreak_html().rstrip("\n"))
        parts.append("")
        parts.append(ep)
        parts.append("")

    OUT_BUILD_MD.write_text("\n".join(parts).strip() + "\n", encoding="utf-8")


def pandoc_build_epub():
    if not COVER_JPG.exists():
        raise FileNotFoundError(f"Capa não encontrada: {COVER_JPG}")

    cmd = [
        "pandoc",
        str(OUT_BUILD_MD),
        "-o",
        str(OUT_EPUB),
        "--toc",
        "--toc-depth=2",
        f"--css={CSS_FILE}",
        f"--epub-cover-image={COVER_JPG}",
        "--metadata",
        f"title={BOOK_TITLE} ({BOOK_SUBTITLE})",
        "--metadata",
        f"author={AUTHOR}",
        "--metadata",
        f"lang={LANG}",
    ]

    # roda pandoc
    subprocess.run(cmd, check=True)


def main():
    print("== ES Rebuild from merge_refine.txt ==")
    raw = read_text(SRC_TXT)

    # 1) inserir headings e pagebreaks por história
    miolo_md, story_titles = insert_story_headings(raw)
    OUT_MIOL_MD.write_text(miolo_md, encoding="utf-8")
    print(f"[OK] Miolo MD: {OUT_MIOL_MD}")
    print(f"[OK] Histórias detectadas: {len(story_titles)} (esperado 12)")

    # 2) css
    build_css()
    print(f"[OK] CSS: {CSS_FILE}")

    # 3) build concatenado (frontmatter + miolo)
    write_build_md(miolo_md)
    print(f"[OK] Build MD: {OUT_BUILD_MD}")

    # 4) pandoc -> epub
    pandoc_build_epub()
    print(f"[OK] EPUB: {OUT_EPUB} ({OUT_EPUB.stat().st_size} bytes)")

    # 5) stamp/checksums (fora do conteúdo)
    RELEASES.mkdir(parents=True, exist_ok=True)
    stamp = RELEASES / f"{BOOK_TITLE.replace(' ', '_')}_ES_BUILD_STAMP_{datetime.now().date()}.txt"
    stamp.write_text(
        "\n".join(
            [
                f"BOOK: {BOOK_TITLE} ({BOOK_SUBTITLE})",
                f"LANG: {LANG}",
                f"SOURCE_TXT: {SRC_TXT}",
                f"SHA256_TXT: (gere com sha256sum)",
                f"OUT_BUILD_MD: {OUT_BUILD_MD}",
                f"OUT_EPUB: {OUT_EPUB}",
                f"STORIES: {len(story_titles)}",
                "OK: headings+pagebreaks+toc+cover+metadata",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[OK] STAMP: {stamp}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        sys.exit(1)
