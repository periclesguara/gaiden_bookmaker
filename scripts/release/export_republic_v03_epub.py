#!/usr/bin/env python3
"""Export The Republic v03 EPUB with corrected frontmatter."""

from __future__ import annotations

import hashlib
import html
import json
import posixpath
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[2]
SOURCE_EPUB = ROOT / "data/builds/book_0027/en/republic_of_plato_BOOK_GLOSSARY.epub"
INTERNAL_OUTPUT = ROOT / "data/builds/book_0027/en/republic_of_plato_v03.epub"
REPORT_PATH = ROOT / "data/builds/book_0027/en/republic_of_plato_v03_export_report.json"
DOWNLOAD_DIR = Path("/home/periclesguara/Downloads/Republic of Plato")
DOWNLOAD_OUTPUT = DOWNLOAD_DIR / "republic_of_plato_v03.epub"
FRONTMATTER_DIR = ROOT / "data/frontmatter/book_0027/en"


BOOK_FILES = [f"EPUB/text/book_{i:02d}.xhtml" for i in range(1, 11)]
FRONTMATTER_FILES = {
    "frontispiece": FRONTMATTER_DIR / "frontispiece.md",
    "copyright": FRONTMATTER_DIR / "copyright.md",
    "about_this_book": FRONTMATTER_DIR / "about_this_book.md",
    "epilogue": FRONTMATTER_DIR / "epilogue.md",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_frontmatter(name: str) -> str:
    path = FRONTMATTER_FILES[name]
    if not path.exists():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8").strip()


def markdown_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    paragraph: list[str] = []

    def flush() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(("p", " ".join(line.strip() for line in paragraph).strip()))
            paragraph = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if line == "::: pagebreak":
            flush()
            continue
        if line.startswith("# "):
            flush()
            blocks.append(("h1", line[2:].strip()))
            continue
        paragraph.append(line)
    flush()
    return blocks


def inline_markdown_to_html(text: str) -> str:
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    return escaped


def xhtml_document(title: str, body_markup: str, epub_type: str = "frontmatter") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE html>\n"
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8" />\n'
        f"  <title>{html.escape(title)}</title>\n"
        '  <link rel="stylesheet" type="text/css" href="../styles/stylesheet1.css" />\n'
        "</head>\n"
        f'<body epub:type="{epub_type}">\n'
        f"{body_markup}\n"
        "</body>\n"
        "</html>\n"
    )


def frontmatter_xhtml(markdown: str, section_id: str, fallback_title: str) -> str:
    blocks = markdown_blocks(markdown)
    title = fallback_title
    body: list[str] = []
    h1_written = False
    for kind, value in blocks:
        if kind == "h1":
            title = value
            body.append(f'<h1 id="{section_id}">{inline_markdown_to_html(value)}</h1>')
            h1_written = True
        else:
            body.append(f"<p>{inline_markdown_to_html(value)}</p>")
    if not h1_written:
        body.insert(0, f'<h1 id="{section_id}">{inline_markdown_to_html(fallback_title)}</h1>')
    return xhtml_document(title, "\n".join(body))


def title_page_xhtml() -> str:
    body = "\n".join(
        [
            '<section id="title-page" epub:type="titlepage">',
            "<h1>The Republic</h1>",
            "<p>Plato of Athens</p>",
            "<p>Modern English Edition</p>",
            "<p>Adapted by Péricles Guará Silva</p>",
            "<p>RinoBooks</p>",
            "<p>Rio de Janeiro, Brazil · 2026</p>",
            "</section>",
        ]
    )
    return xhtml_document("Title Page", body)


def extract_epub(epub: Path, workdir: Path) -> None:
    with zipfile.ZipFile(epub) as zf:
        zf.extractall(workdir)


def write_epub(source_dir: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w") as zf:
        mimetype = source_dir / "mimetype"
        if mimetype.exists():
            zf.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)
        for path in sorted(source_dir.rglob("*")):
            if path.is_dir() or path.name == "mimetype":
                continue
            rel = path.relative_to(source_dir).as_posix()
            zf.write(path, rel, compress_type=zipfile.ZIP_DEFLATED)


def update_frontmatter(workdir: Path) -> None:
    (workdir / "EPUB/text/title_page.xhtml").write_text(title_page_xhtml(), encoding="utf-8")
    (workdir / "EPUB/text/frontispiece.xhtml").write_text(
        frontmatter_xhtml(read_frontmatter("frontispiece"), "frontispiece", "Frontispiece"),
        encoding="utf-8",
    )
    (workdir / "EPUB/text/copyright.xhtml").write_text(
        frontmatter_xhtml(read_frontmatter("copyright"), "copyright", "Copyright"),
        encoding="utf-8",
    )
    (workdir / "EPUB/text/about_this_book.xhtml").write_text(
        frontmatter_xhtml(read_frontmatter("about_this_book"), "about-this-book", "About This Book"),
        encoding="utf-8",
    )
    (workdir / "EPUB/text/epilogue.xhtml").write_text(
        frontmatter_xhtml(read_frontmatter("epilogue"), "epilogue", "Epilogue"),
        encoding="utf-8",
    )


def ensure_manifest_item(soup: BeautifulSoup, manifest, *, item_id: str, href: str, media_type: str) -> None:
    item = soup.find("item", id=item_id)
    if item is None:
        item = soup.new_tag("item")
        item["id"] = item_id
        manifest.append(item)
    item["href"] = href
    item["media-type"] = media_type


def ensure_spine_item_after(soup: BeautifulSoup, spine, *, idref: str, after_idref: str) -> None:
    existing = soup.find("itemref", idref=idref)
    if existing is not None:
        existing.extract()
    itemref = soup.new_tag("itemref")
    itemref["idref"] = idref
    after = soup.find("itemref", idref=after_idref)
    if after is not None:
        after.insert_after(itemref)
    else:
        spine.append(itemref)


def update_opf_metadata(workdir: Path) -> bool:
    path = workdir / "EPUB/content.opf"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
    metadata = soup.find("metadata")
    manifest = soup.find("manifest")
    spine = soup.find("spine")
    if metadata is None:
        return False
    if manifest is None or spine is None:
        return False

    def set_dc(name: str, value: str, attrs: dict[str, str] | None = None) -> None:
        node = metadata.find(name)
        if node is None:
            node = soup.new_tag(name)
            metadata.append(node)
        node.string = value
        for key, val in (attrs or {}).items():
            node[key] = val

    set_dc("dc:title", "The Republic")
    set_dc("dc:language", "en")
    set_dc("dc:creator", "Plato of Athens")
    set_dc("dc:publisher", "RinoBooks")

    contributor = metadata.find("dc:contributor", id="epub-contributor-1")
    if contributor is None:
        contributor = soup.new_tag("dc:contributor")
        contributor["id"] = "epub-contributor-1"
        metadata.append(contributor)
    contributor.string = "Péricles Guará Silva"

    role = None
    for meta in metadata.find_all("meta"):
        if meta.get("refines") == "#epub-contributor-1" and meta.get("property") == "role":
            role = meta
            break
    if role is None:
        role = soup.new_tag("meta")
        role["refines"] = "#epub-contributor-1"
        role["property"] = "role"
        role["scheme"] = "marc:relators"
        metadata.append(role)
    role.string = "adp"

    modified = metadata.find("meta", attrs={"property": "dcterms:modified"})
    if modified is None:
        modified = soup.new_tag("meta")
        modified["property"] = "dcterms:modified"
        metadata.append(modified)
    modified.string = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    ensure_manifest_item(
        soup,
        manifest,
        item_id="text_epilogue_xhtml",
        href="text/epilogue.xhtml",
        media_type="application/xhtml+xml",
    )
    ensure_spine_item_after(
        soup,
        spine,
        idref="text_epilogue_xhtml",
        after_idref="text_glossary_xhtml",
    )

    path.write_text(str(soup), encoding="utf-8")
    return True


def upsert_nav_epilogue(workdir: Path) -> bool:
    path = workdir / "EPUB/nav.xhtml"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
    toc = soup.find("nav", {"epub:type": "toc"}) or soup.find("nav", id="toc")
    if toc is None:
        return False
    ol = toc.find("ol")
    if ol is None:
        return False
    for link in soup.find_all("a", href="text/epilogue.xhtml#epilogue"):
        parent = link.find_parent("li")
        if parent is not None:
            parent.extract()
    li = soup.new_tag("li")
    a = soup.new_tag("a")
    a["href"] = "text/epilogue.xhtml#epilogue"
    a.string = "Epilogue"
    li.append(a)
    ol.append(li)
    path.write_text(str(soup), encoding="utf-8")
    return True


def upsert_ncx_epilogue(workdir: Path) -> bool:
    path = workdir / "EPUB/toc.ncx"
    if not path.exists():
        return False
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
    nav_map = soup.find("navMap")
    if nav_map is None:
        return False
    for content in soup.find_all("content", src="text/epilogue.xhtml#epilogue"):
        parent = content.find_parent("navPoint")
        if parent is not None:
            parent.extract()
    play_orders = [
        int(node.get("playOrder", "0"))
        for node in soup.find_all("navPoint")
        if str(node.get("playOrder", "")).isdigit()
    ]
    order = max(play_orders or [0]) + 1
    nav_point = soup.new_tag("navPoint")
    nav_point["id"] = f"navPoint-{order}"
    nav_point["playOrder"] = str(order)
    nav_label = soup.new_tag("navLabel")
    label_text = soup.new_tag("text")
    label_text.string = "Epilogue"
    content = soup.new_tag("content")
    content["src"] = "text/epilogue.xhtml#epilogue"
    nav_label.append(label_text)
    nav_point.append(nav_label)
    nav_point.append(content)
    nav_map.append(nav_point)
    path.write_text(str(soup), encoding="utf-8")
    return True


def update_nav_titles_and_epilogue(workdir: Path) -> bool:
    changed = False
    for rel in ("EPUB/nav.xhtml", "EPUB/toc.ncx"):
        path = workdir / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        updated = text.replace("Plato: The Republic", "The Republic")
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed = True
    nav_epilogue = upsert_nav_epilogue(workdir)
    ncx_epilogue = upsert_ncx_epilogue(workdir)
    changed = changed or nav_epilogue or ncx_epilogue
    return changed


def collect_ids(workdir: Path) -> tuple[dict[str, set[str]], list[str]]:
    ids_by_file: dict[str, set[str]] = {}
    duplicates: list[str] = []
    for path in sorted((workdir / "EPUB").rglob("*.xhtml")):
        rel = path.relative_to(workdir).as_posix()
        ids: set[str] = set()
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
        for node in soup.find_all(attrs={"id": True}):
            node_id = str(node["id"])
            if node_id in ids:
                duplicates.append(f"{rel}#{node_id}")
            ids.add(node_id)
        ids_by_file[rel] = ids
    return ids_by_file, sorted(set(duplicates))


def resolve_href(source_rel: str, href: str) -> tuple[str, str]:
    target, _, fragment = href.partition("#")
    base_dir = posixpath.dirname(source_rel)
    target_rel = posixpath.normpath(posixpath.join(base_dir, target)) if target else source_rel
    return target_rel, fragment


def validate_epub_dir(workdir: Path) -> dict[str, object]:
    ids_by_file, duplicate_ids = collect_ids(workdir)
    existing = {path.relative_to(workdir).as_posix() for path in workdir.rglob("*") if path.is_file()}
    broken_links: list[str] = []
    parse_errors: list[str] = []

    for path in sorted((workdir / "EPUB").rglob("*.xhtml")):
        rel = path.relative_to(workdir).as_posix()
        try:
            soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
        except Exception as exc:  # pragma: no cover - defensive reporting
            parse_errors.append(f"{rel}: {exc}")
            continue
        for anchor in soup.find_all("a", href=True):
            href = str(anchor["href"])
            if re.match(r"^[a-z]+:", href):
                continue
            target_rel, fragment = resolve_href(rel, href)
            if target_rel not in existing:
                broken_links.append(f"{rel}: missing target {href}")
            elif fragment and fragment not in ids_by_file.get(target_rel, set()):
                broken_links.append(f"{rel}: missing fragment {href}")

    opf = BeautifulSoup((workdir / "EPUB/content.opf").read_text(encoding="utf-8"), "xml")
    manifest_items = {item.get("id"): item.get("href") for item in opf.find_all("item")}
    spine_refs = [itemref.get("idref") for itemref in opf.find_all("itemref")]
    missing_spine = [idref for idref in spine_refs if idref not in manifest_items]
    missing_manifest_files = []
    for item_id, href in manifest_items.items():
        if not href:
            continue
        target = posixpath.normpath(posixpath.join("EPUB", href))
        if target not in existing:
            missing_manifest_files.append(f"{item_id}: {href}")

    book_order_ok = True
    epilogue_after_glossary = False
    spine_hrefs = [manifest_items.get(idref, "") for idref in spine_refs]
    if "text/book_10.xhtml" in spine_hrefs and "text/glossary.xhtml" in spine_hrefs:
        book_order_ok = spine_hrefs.index("text/glossary.xhtml") > spine_hrefs.index("text/book_10.xhtml")
    if "text/glossary.xhtml" in spine_hrefs and "text/epilogue.xhtml" in spine_hrefs:
        epilogue_after_glossary = spine_hrefs.index("text/epilogue.xhtml") > spine_hrefs.index("text/glossary.xhtml")

    body_ref_count = 0
    glossary_entries = 0
    backlink_count = 0
    if (workdir / "EPUB/text/glossary.xhtml").exists():
        glossary = BeautifulSoup((workdir / "EPUB/text/glossary.xhtml").read_text(encoding="utf-8"), "xml")
        glossary_entries = len([s for s in glossary.find_all("section") if str(s.get("id", "")).startswith("glossary-")])
        backlink_count = len(glossary.find_all("a", href=lambda h: h and h.startswith("book_")))
    for book in BOOK_FILES:
        text = (workdir / book).read_text(encoding="utf-8")
        body_ref_count += len(re.findall(r'id="ref-', text))

    return {
        "parse_errors": parse_errors,
        "duplicate_ids": duplicate_ids,
        "broken_links": sorted(set(broken_links)),
        "missing_spine_manifest_refs": missing_spine,
        "missing_manifest_files": missing_manifest_files,
        "glossary_after_book_10": book_order_ok,
        "epilogue_present": (workdir / "EPUB/text/epilogue.xhtml").exists(),
        "epilogue_in_manifest": "text/epilogue.xhtml" in manifest_items.values(),
        "epilogue_after_glossary": epilogue_after_glossary,
        "glossary_entries": glossary_entries,
        "glossary_body_references": body_ref_count,
        "glossary_backlinks": backlink_count,
    }


def main() -> None:
    if not SOURCE_EPUB.exists():
        raise FileNotFoundError(SOURCE_EPUB)

    source_hash = sha256(SOURCE_EPUB)
    with tempfile.TemporaryDirectory(prefix="republic_v03_") as tmp:
        workdir = Path(tmp)
        extract_epub(SOURCE_EPUB, workdir)
        update_frontmatter(workdir)
        opf_updated = update_opf_metadata(workdir)
        nav_titles_updated = update_nav_titles_and_epilogue(workdir)
        validation = validate_epub_dir(workdir)
        write_epub(workdir, INTERNAL_OUTPUT)

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(INTERNAL_OUTPUT, DOWNLOAD_OUTPUT)

    blocked = any(
        validation[key]
        for key in (
            "parse_errors",
            "duplicate_ids",
            "broken_links",
            "missing_spine_manifest_refs",
            "missing_manifest_files",
        )
    ) or not validation["epilogue_present"] or not validation["epilogue_in_manifest"]
    final_status = "READY_FOR_KINDLE_PREVIEWER" if not blocked else "BLOCKED_NEEDS_FIX"
    report = {
        "source_epub": str(SOURCE_EPUB.relative_to(ROOT)),
        "source_epub_sha256": source_hash,
        "internal_output_epub": str(INTERNAL_OUTPUT.relative_to(ROOT)),
        "download_output_epub": str(DOWNLOAD_OUTPUT),
        "download_output_sha256": sha256(DOWNLOAD_OUTPUT),
        "frontmatter_sources": {name: str(path.relative_to(ROOT)) for name, path in FRONTMATTER_FILES.items()},
        "frontmatter_updated": [
            "title_page.xhtml",
            "frontispiece.xhtml",
            "copyright.xhtml",
            "about_this_book.xhtml",
            "epilogue.xhtml",
        ],
        "metadata_updated": {
            "dc:title": "The Republic",
            "dc:language": "en",
            "dc:creator": "Plato of Athens",
            "dc:contributor": "Péricles Guará Silva",
            "dc:publisher": "RinoBooks",
        },
        "opf_updated": opf_updated,
        "nav_titles_updated": nav_titles_updated,
        "validation": validation,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "final_status": final_status,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("REPUBLIC V03 EXPORT COMPLETE")
    print()
    print("Generated:")
    print(str(INTERNAL_OUTPUT.relative_to(ROOT)))
    print(str(DOWNLOAD_OUTPUT))
    print(str(REPORT_PATH.relative_to(ROOT)))
    print()
    print("Status:")
    print(final_status)
    if blocked:
        print(json.dumps(validation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
