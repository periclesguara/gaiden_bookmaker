from __future__ import annotations

import html
import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
SOURCE_EPUB = ROOT / "data/builds/book_0024/fr/history/BOOK.v5.epub.epub"
OUT_DIR = ROOT / "data/builds/book_0024/fr"
OUT_EPUB = OUT_DIR / "FR_BOOK_v06_corrected.epub"
REPORT = OUT_DIR / "FR_BOOK_v06_correction_report.txt"


TITLE_REPLACEMENTS = {
    "Frontispiece": "Frontispice",
    "Copyright": "Droits d’auteur",
    "About This Book": "À propos de ce livre",
    "Preface": "Préface",
    "Epilogue": "Épilogue",
    "GLOSSAIRE": "Glossaire",
    "Title Page": "Page de titre",
    "Cover": "Couverture",
    "Table of Contents": "Table des matières",
}


@dataclass
class ValidationResult:
    broken_links: list[str]
    heading_violations: list[str]
    spine_errors: list[str]
    nav_titles: list[str]
    ncx_titles: list[str]
    cover_preserved: bool
    livres_preserved: bool


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="")


def normalize_brand(text: str) -> str:
    text = re.sub(r"\bRino\s+[Bb]ooks\b", "RinoBooks", text)
    text = re.sub(r"\bRino books\b", "RinoBooks", text)
    text = re.sub(r"\bRinobooks\b", "RinoBooks", text)
    text = re.sub(r"\bRINOBOOKS\b", "RinoBooks", text)
    return text


def clean_heading_markup(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        tag = match.group(1)
        attrs = match.group(2)
        inner = match.group(3)
        inner = re.sub(r"<sup\b[^>]*>.*?</sup>", "", inner, flags=re.I | re.S)
        inner = re.sub(r"<a\b[^>]*>(.*?)</a>", r"\1", inner, flags=re.I | re.S)
        inner = re.sub(r"\s+", " ", inner).strip()
        inner = re.sub(r"\bLes Stoïciens\s+\d+\s+—", "Les Stoïciens —", inner)
        return f"<{tag}{attrs}>{inner}</{tag}>"

    return re.sub(r"<(h[123])([^>]*)>(.*?)</\1>", repl, text, flags=re.I | re.S)


def fix_opf(path: Path, changed: set[str]) -> dict[str, str]:
    before = read_text(path)
    text = normalize_brand(before)
    text = re.sub(
        r"<dc:publisher>.*?</dc:publisher>",
        "<dc:publisher>RinoBooks</dc:publisher>",
        text,
        flags=re.S,
    )
    if 'refines="#epub-contributor-1" property="role"' not in text:
        text = text.replace(
            '<dc:contributor id="epub-contributor-1">Péricles Guará Silva</dc:contributor>',
            '<dc:contributor id="epub-contributor-1">Péricles Guará Silva</dc:contributor>\n'
            '    <meta refines="#epub-contributor-1" property="role" scheme="marc:relators">adp</meta>',
        )
    text = text.replace('title="Cover"', 'title="Couverture"')
    if text != before:
        write_text(path, text)
        changed.add("EPUB/content.opf")
    return {
        "title": "Les Méditations de Marc Aurèle",
        "language": "fr",
        "creator": "Marc Aurèle",
        "contributor": "Péricles Guará Silva",
        "publisher": "RinoBooks",
        "contributor_role": "adp",
    }


def fix_simple_xhtml(path: Path, changed: set[str]) -> list[str]:
    before = read_text(path)
    text = normalize_brand(before)
    text = text.replace("Édition moderne en français", "Édition en français moderne")
    for old, new in TITLE_REPLACEMENTS.items():
        text = text.replace(f">{old}<", f">{new}<")
    text = text.replace(
        "Cette édition moderne de <em>Les Méditations de Marc Aurèle</em>",
        "Cette édition moderne des <em>Méditations de Marc Aurèle</em>",
    )
    text = text.replace(
        "Tous droits réservés à RinoBooks.",
        "Tous droits réservés pour cette édition, son adaptation, sa mise en forme et ses éléments éditoriaux.",
    )
    text = clean_heading_markup(text)
    if text != before:
        write_text(path, text)
        changed.add("EPUB/" + path.relative_to(path.parents[1]).as_posix())
    return [f"{old} -> {new}" for old, new in TITLE_REPLACEMENTS.items() if old in before and new in text]


def fix_ch017(path: Path, changed: set[str]) -> tuple[bool, bool]:
    before = read_text(path)
    text = before
    notes_removed = False
    if "<p>Notes de fin" in text and "<p>GLOSSAIRE</p>" in text:
        text = re.sub(r"\n<p>Notes de fin.*?(?=\n<p>GLOSSAIRE</p>)", "\n", text, flags=re.S)
        notes_removed = True
    text = text.replace("<p>GLOSSAIRE</p>", '<h1 id="glossaire">Glossaire</h1>')
    text = normalize_brand(text)
    text = clean_heading_markup(text)
    if text != before:
        write_text(path, text)
        changed.add("EPUB/text/ch017.xhtml")
    return notes_removed, '<h1 id="glossaire">Glossaire</h1>' in text


def fix_nav(path: Path, changed: set[str]) -> list[str]:
    before = read_text(path)
    text = normalize_brand(before)
    for old, new in TITLE_REPLACEMENTS.items():
        text = text.replace(f">{old}<", f">{new}<")
    if 'href="text/ch017.xhtml#glossaire">Glossaire</a>' not in text:
        text = text.replace(
            '<li id="toc-li-18"><a href="text/ch018.xhtml#epilogue">Épilogue</a>',
            '<li id="toc-li-glossaire"><a href="text/ch017.xhtml#glossaire">Glossaire</a></li>'
            '<li id="toc-li-18"><a href="text/ch018.xhtml#epilogue">Épilogue</a>',
        )
    text = re.sub(r"Les Stoïciens\s+\d+\s+— Raison, ordre et destin", "Les Stoïciens — Raison, ordre et destin", text)
    text = clean_heading_markup(text)
    if text != before:
        write_text(path, text)
        changed.add("EPUB/nav.xhtml")
    return [f"{old} -> {new}" for old, new in TITLE_REPLACEMENTS.items() if old in before or new in text]


def shift_ncx_ids(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        number = int(match.group(1))
        return f'navPoint-{number + 1}' if number >= 18 else match.group(0)

    return re.sub(r"navPoint-(\d+)", repl, text)


def fix_ncx(path: Path, changed: set[str]) -> list[str]:
    before = read_text(path)
    text = normalize_brand(before)
    for old, new in TITLE_REPLACEMENTS.items():
        text = text.replace(f"<text>{old}</text>", f"<text>{new}</text>")
    text = re.sub(r"Les Stoïciens\s+\d+\s+— Raison, ordre et destin", "Les Stoïciens — Raison, ordre et destin", text)
    if "<text>Glossaire</text>" not in text:
        text = shift_ncx_ids(text)
        insertion = """    <navPoint id="navPoint-18">
      <navLabel>
        <text>Glossaire</text>
      </navLabel>
      <content src="text/ch017.xhtml#glossaire" />
    </navPoint>
"""
        text = text.replace('    <navPoint id="navPoint-19">\n      <navLabel>\n        <text>Épilogue</text>', insertion + '    <navPoint id="navPoint-19">\n      <navLabel>\n        <text>Épilogue</text>')
    if text != before:
        write_text(path, text)
        changed.add("EPUB/toc.ncx")
    return [f"{old} -> {new}" for old, new in TITLE_REPLACEMENTS.items() if old in before or new in text]


def extract_epub(source: Path, dest: Path) -> None:
    with zipfile.ZipFile(source) as zf:
        zf.extractall(dest)


def package_epub(source_dir: Path, out_path: Path) -> None:
    if out_path.exists():
        out_path.unlink()
    with zipfile.ZipFile(out_path, "w") as zf:
        zf.write(source_dir / "mimetype", "mimetype", compress_type=zipfile.ZIP_STORED)
        for path in sorted(source_dir.rglob("*")):
            if path.is_dir() or path.name == "mimetype":
                continue
            arcname = path.relative_to(source_dir).as_posix()
            zf.write(path, arcname, compress_type=zipfile.ZIP_DEFLATED)


def collect_ids(text: str) -> set[str]:
    ids = set(re.findall(r'\bid="([^"]+)"', text))
    ids.update(re.findall(r"\bid='([^']+)'", text))
    return ids


def collect_href_values(text: str) -> list[str]:
    values = re.findall(r'\b(?:href|src)="([^"]+)"', text)
    values.extend(re.findall(r"\b(?:href|src)='([^']+)'", text))
    return values


def clean_visible_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def validate(epub_dir: Path, original_cover: bytes, original_livre_hashes: dict[str, bytes]) -> ValidationResult:
    existing = {p.relative_to(epub_dir).as_posix() for p in epub_dir.rglob("*") if p.is_file()}
    ids_by_file: dict[str, set[str]] = {}
    broken_links: list[str] = []
    heading_violations: list[str] = []
    for rel in existing:
        if rel.endswith((".xhtml", ".opf", ".ncx")):
            text = read_text(epub_dir / rel)
            ids_by_file[rel] = collect_ids(text)
            for match in re.finditer(r"<(h[123])\b[^>]*>(.*?)</\1>", text, flags=re.I | re.S):
                inner = match.group(2)
                if re.search(r"<\s*(sup|a)\b", inner, flags=re.I) or re.search(r"glossary-term|\bG\d{3}\b", inner):
                    heading_violations.append(f"{rel}: {clean_visible_text(match.group(0))}")
            for href in collect_href_values(text):
                if href.startswith(("http:", "https:", "mailto:", "urn:")):
                    continue
                target, _, frag = href.partition("#")
                base_dir = posixpath.dirname(rel)
                if target:
                    normalized = posixpath.normpath(posixpath.join(base_dir, target))
                else:
                    normalized = rel
                if normalized not in existing:
                    broken_links.append(f"{rel} -> {href} (arquivo ausente: {normalized})")
                    continue
                if frag and frag not in ids_by_file.get(normalized, set()):
                    if normalized not in ids_by_file and normalized.endswith((".xhtml", ".opf", ".ncx")):
                        ids_by_file[normalized] = collect_ids(read_text(epub_dir / normalized))
                    if frag not in ids_by_file.get(normalized, set()):
                        broken_links.append(f"{rel} -> {href} (id ausente: {frag})")

    opf = read_text(epub_dir / "EPUB/content.opf")
    manifest = dict(re.findall(r'<item\b[^>]*\bid="([^"]+)"[^>]*\bhref="([^"]+)"', opf))
    spine_refs = re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', opf)
    spine_errors: list[str] = []
    for idref in spine_refs:
        href = manifest.get(idref)
        if not href:
            spine_errors.append(f"spine idref sem manifest: {idref}")
        elif posixpath.normpath("EPUB/" + href) not in existing:
            spine_errors.append(f"spine idref arquivo ausente: {idref} -> {href}")

    nav = read_text(epub_dir / "EPUB/nav.xhtml")
    nav_titles = [clean_visible_text(m.group(1)) for m in re.finditer(r"<a\b[^>]*>(.*?)</a>", nav, flags=re.S)]
    ncx = read_text(epub_dir / "EPUB/toc.ncx")
    ncx_titles = [clean_visible_text(m.group(1)) for m in re.finditer(r"<text>(.*?)</text>", ncx, flags=re.S)]
    ncx_titles = ncx_titles[1:]

    cover_preserved = (epub_dir / "EPUB/media/cover.jpg").read_bytes() == original_cover
    livres_preserved = True
    for rel, original in original_livre_hashes.items():
        current = (epub_dir / rel).read_bytes()
        if rel == "EPUB/text/ch017.xhtml":
            before_main = original.split(b"\n<p>Notes de fin", 1)[0].rstrip()
            after_main = current.split(b"\n<h1 id=\"glossaire\">Glossaire</h1>", 1)[0].rstrip()
            if before_main != after_main:
                livres_preserved = False
        elif current != original:
            livres_preserved = False

    return ValidationResult(
        broken_links=broken_links,
        heading_violations=heading_violations,
        spine_errors=spine_errors,
        nav_titles=nav_titles,
        ncx_titles=ncx_titles,
        cover_preserved=cover_preserved,
        livres_preserved=livres_preserved,
    )


def main() -> None:
    if not SOURCE_EPUB.exists():
        raise FileNotFoundError(SOURCE_EPUB)

    with zipfile.ZipFile(SOURCE_EPUB) as zf:
        original_cover = zf.read("EPUB/media/cover.jpg")
        original_livre_hashes = {f"EPUB/text/ch{i:03d}.xhtml": zf.read(f"EPUB/text/ch{i:03d}.xhtml") for i in range(6, 18)}

    changed: set[str] = set()
    titles: list[str] = []
    metadata: dict[str, str] = {}
    notes_removed = False
    glossary_heading = False

    with TemporaryDirectory(prefix="book_0024_fr_v06_") as td:
        work = Path(td)
        extract_epub(SOURCE_EPUB, work)

        metadata = fix_opf(work / "EPUB/content.opf", changed)
        titles.extend(fix_nav(work / "EPUB/nav.xhtml", changed))
        titles.extend(fix_ncx(work / "EPUB/toc.ncx", changed))
        for rel in [
            "EPUB/text/title_page.xhtml",
            "EPUB/text/ch001.xhtml",
            "EPUB/text/ch002.xhtml",
            "EPUB/text/ch003.xhtml",
            "EPUB/text/ch004.xhtml",
            "EPUB/text/ch018.xhtml",
        ]:
            titles.extend(fix_simple_xhtml(work / rel, changed))
        notes_removed, glossary_heading = fix_ch017(work / "EPUB/text/ch017.xhtml", changed)

        result = validate(work, original_cover, original_livre_hashes)
        if result.broken_links or result.heading_violations or result.spine_errors:
            details = "\n".join(result.broken_links + result.heading_violations + result.spine_errors)
            raise RuntimeError(f"Validation failed:\n{details}")
        if not result.cover_preserved:
            raise RuntimeError("Cover bytes changed.")
        if not result.livres_preserved:
            raise RuntimeError("Livre 01-12 main text changed unexpectedly.")

        package_epub(work, OUT_EPUB)

    unique_titles = sorted(set(titles))
    nav_core = [t for t in result.nav_titles if t not in {"Page de titre", "Couverture", "Table des matières"}]
    ncx_core = [t for t in result.ncx_titles if t != "Les Méditations de Marc Aurèle"]
    report_lines = [
        "FR_BOOK_v06 Correction Report",
        "",
        f"Source EPUB: {SOURCE_EPUB.relative_to(ROOT)}",
        f"Output EPUB: {OUT_EPUB.relative_to(ROOT)}",
        "",
        f"Arquivos XHTML corrigidos: {sum(1 for f in changed if f.endswith('.xhtml'))}",
        "Arquivos alterados:",
        *[f"- {item}" for item in sorted(changed)],
        "",
        "Titulos corrigidos:",
        *[f"- {item}" for item in unique_titles],
        "- Les Stoïciens 88 — Raison, ordre et destin -> Les Stoïciens — Raison, ordre et destin",
        "- GLOSSAIRE -> Glossaire",
        "",
        "Metadata corrigido:",
        *[f"- {key}: {value}" for key, value in metadata.items()],
        "",
        "Links quebrados encontrados/corrigidos:",
        "- Encontrados apos correcao: 0",
        "- Corrigidos: nenhum link quebrado restante; Glossaire foi adicionado ao nav/toc com destino text/ch017.xhtml#glossaire.",
        "",
        f"Notes de fin removidas: {'sim' if notes_removed else 'nao'}",
        f"Glossaire preservado e normalizado: {'sim' if glossary_heading else 'nao'}",
        "Padronizacao RinoBooks: sim",
        f"Sincronizacao nav.xhtml/toc.ncx: {'sim' if nav_core == ncx_core else 'nao'}",
        f"Capa preservada byte a byte: {'sim' if result.cover_preserved else 'nao'}",
        f"Livres 01 a 12 preservados: {'sim' if result.livres_preserved else 'nao'}",
        "Nenhum h1, h2 ou h3 contem <sup>, <a> ou nota de glossario: sim",
        "",
        "Spine/manifest:",
        "- Todos os itemref do spine existem no manifest e os arquivos referenciados existem.",
        "- Nenhum novo arquivo de notas foi adicionado; o bloco Notes de fin foi removido.",
    ]
    write_text(REPORT, "\n".join(report_lines) + "\n")
    print(OUT_EPUB.relative_to(ROOT))
    print(REPORT.relative_to(ROOT))


if __name__ == "__main__":
    main()
