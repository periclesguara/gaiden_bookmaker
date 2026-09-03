from __future__ import annotations

import argparse
import hashlib
import html
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET


XHTML_NS = "http://www.w3.org/1999/xhtml"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"

CHAPTERS = [
    ("Chapter 01 — The Blasted Heath", "chapter_001_opening.xhtml", "chapter-001"),
    ("Chapter 02 — The Meteorite", "chapter_002_opening.xhtml", "chapter-002"),
    ("Chapter 03 — The Tainted Harvest", "chapter_003_opening.xhtml", "chapter-003"),
    ("Chapter 04 — The Strange Days", "chapter_004_opening.xhtml", "chapter-004"),
    ("Chapter 05 — What Lived in the Well", "chapter_005_opening.xhtml", "chapter-005"),
    ("Chapter 06 — The Colour Ascends", "chapter_006_opening.xhtml", "chapter-006"),
]

TOC = [
    ("About This Book", "about_this_edition.xhtml", "about-this-edition"),
    *CHAPTERS,
    ("The End", "the_end.xhtml", "the-end"),
]

ALT_TEXTS = {
    "chapter_001_opening.xhtml": "A solitary surveyor overlooking the barren blasted heath and its abandoned well.",
    "chapter_002_opening.xhtml": "Miskatonic professors examining the meteorite beside the Gardner farmhouse.",
    "chapter_003_opening.xhtml": "Unnatural vegetation spreading through the Gardner orchard as a frightened horse recoils.",
    "chapter_004_opening.xhtml": "Ammi Pierce entering the dark Gardner house as an unnatural color gathers in the attic.",
    "chapter_005_opening.xhtml": "Investigators watching the luminous well from inside the Gardner farmhouse.",
    "chapter_006_opening.xhtml": "An alien color rising from the Gardner farm into the storm-darkened sky.",
}

SPINE = [
    "text/cover.xhtml",
    "text/frontispiece.xhtml",
    "text/title_page.xhtml",
    "text/copyright.xhtml",
    "text/about_this_edition.xhtml",
    "text/contents.xhtml",
    "text/chapter_001_opening.xhtml",
    "text/chapter_001.xhtml",
    "text/chapter_002_opening.xhtml",
    "text/chapter_002.xhtml",
    "text/chapter_003_opening.xhtml",
    "text/chapter_003.xhtml",
    "text/chapter_004_opening.xhtml",
    "text/chapter_004.xhtml",
    "text/chapter_005_opening.xhtml",
    "text/chapter_005.xhtml",
    "text/chapter_006_opening.xhtml",
    "text/chapter_006.xhtml",
    "text/the_end.xhtml",
]

PROHIBITED = [
    "MantaQuest",
    "MantaQuest Editorial",
    "Modern English Translation",
    "Translation and Adaptation",
    "© 2026 Wrecked Alien Machines",
    ">Rinobooks<",
    "Nothing has been summarized or removed.",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def document(title: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">\n'
        f'<head><meta charset="utf-8"/><title>{html.escape(title)}</title>'
        '<link rel="stylesheet" type="text/css" href="../styles/gaiden-premium.css"/></head>\n'
        f"{body}\n</html>\n"
    )


def write_frontmatter(root: Path) -> None:
    text_dir = root / "EPUB/text"
    frontispiece = (
        '<body epub:type="frontmatter"><section epub:type="frontispiece" '
        'class="frontmatter frontispiece" id="frontispiece">'
        '<p class="no-indent centered">WRECKED ALIEN MACHINE</p>'
        '<p class="no-indent centered">THE COLOUR OUT OF SPACE</p>'
        '<p class="no-indent centered">Illustrated Edition · Modern English</p>'
        '<p class="no-indent centered">H. P. LOVECRAFT</p>'
        '<p class="no-indent centered">Modern English Adaptation<br/>Wrecked Alien Machines Editorial</p>'
        '<p class="no-indent centered">Editor<br/>Péricles Guará Silva</p>'
        '<p class="no-indent centered">RINOBOOKS</p>'
        '<p class="no-indent centered">Rio de Janeiro – RJ<br/>Brazil</p>'
        '<p class="no-indent centered">2026</p></section></body>'
    )
    (text_dir / "frontispiece.xhtml").write_text(document("Frontispiece", frontispiece), encoding="utf-8")

    copyright_body = (
        '<body epub:type="frontmatter"><section epub:type="copyright-page" '
        'class="copyright-page" id="copyright"><h1>Copyright</h1>'
        '<p class="no-indent centered">THE COLOUR OUT OF SPACE<br/>Illustrated Edition · Modern English</p>'
        '<p class="no-indent centered">Original Work<br/>H. P. Lovecraft</p>'
        '<p class="no-indent centered">Modern English Adaptation<br/>Wrecked Alien Machines Editorial</p>'
        '<p class="no-indent centered">Editor<br/>Péricles Guará Silva</p>'
        '<p class="no-indent centered">Modern English adaptation, editorial material, illustrations, and cover artwork © 2026 RinoBooks.</p>'
        '<p class="no-indent centered">The original work by H. P. Lovecraft is in the public domain where applicable. Copyright protection applies exclusively to the adaptation, editorial contributions, illustrations, cover design, typography, and arrangement created for this edition. No claim is made to the underlying public-domain text.</p>'
        '<p class="no-indent centered">All rights reserved for the original material created specifically for this edition.</p>'
        '<p class="no-indent centered">Published by Wrecked Alien Machine, an imprint of RinoBooks.</p>'
        '<p class="no-indent centered">Rio de Janeiro – RJ<br/>Brazil<br/>2026</p>'
        '</section></body>'
    )
    (text_dir / "copyright.xhtml").write_text(document("Copyright", copyright_body), encoding="utf-8")

    about_path = text_dir / "about_this_edition.xhtml"
    about = about_path.read_text(encoding="utf-8")
    old = "Nothing has been summarized or removed."
    new = "No major scene, character, or narrative movement has been removed."
    if about.count(old) != 1:
        raise ValueError(f"Expected exactly one About This Book sentence, found {about.count(old)}")
    about_path.write_text(about.replace(old, new), encoding="utf-8")


def write_navigation(root: Path, identifier: str) -> None:
    text_dir = root / "EPUB/text"
    visible_items = "".join(
        f'<li class="contents-chapter"><a href="{filename}#{section_id}">{html.escape(label)}</a></li>'
        for label, filename, section_id in TOC
    )
    contents_body = (
        '<body epub:type="frontmatter"><section epub:type="toc" class="frontmatter contents-page" '
        f'id="contents"><h1>Contents</h1><ol class="contents-list">{visible_items}</ol></section></body>'
    )
    (text_dir / "contents.xhtml").write_text(document("Contents", contents_body), encoding="utf-8")

    nav_items = "".join(
        f'<li><a href="text/{filename}#{section_id}">{html.escape(label)}</a></li>'
        for label, filename, section_id in TOC
    )
    nav = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        'lang="en" xml:lang="en"><head><meta charset="utf-8"/><title>Contents</title>'
        '<link rel="stylesheet" type="text/css" href="styles/gaiden-premium.css"/></head>'
        f'<body epub:type="frontmatter"><nav epub:type="toc" id="toc"><h1>Contents</h1><ol>{nav_items}</ol></nav>'
        '<nav epub:type="landmarks" id="landmarks"><h2>Landmarks</h2><ol>'
        '<li><a epub:type="cover" href="text/cover.xhtml#cover">Cover</a></li>'
        '<li><a epub:type="titlepage" href="text/title_page.xhtml#title-page">Title Page</a></li>'
        '<li><a epub:type="toc" href="text/contents.xhtml#contents">Table of Contents</a></li>'
        '<li><a epub:type="bodymatter" href="text/chapter_001_opening.xhtml#chapter-001">Beginning of Body Matter</a></li>'
        '</ol></nav></body></html>\n'
    )
    (root / "EPUB/nav.xhtml").write_text(nav, encoding="utf-8")

    points = "".join(
        f'<navPoint id="nav-{index}" playOrder="{index}"><navLabel><text>{html.escape(label)}</text></navLabel>'
        f'<content src="text/{filename}#{section_id}"/></navPoint>'
        for index, (label, filename, section_id) in enumerate(TOC, start=1)
    )
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        f'<head><meta name="dtb:uid" content="{html.escape(identifier)}"/></head>'
        '<docTitle><text>The Colour Out of Space</text></docTitle>'
        f'<navMap>{points}</navMap></ncx>\n'
    )
    (root / "EPUB/toc.ncx").write_text(ncx, encoding="utf-8")


def update_openings_and_css(root: Path) -> None:
    for filename, alt_text in ALT_TEXTS.items():
        path = root / "EPUB/text" / filename
        value = path.read_text(encoding="utf-8")
        updated, count = re.subn(r'alt="Illustration for Chapter \d+"', f'alt="{html.escape(alt_text)}"', value)
        if count != 1:
            raise ValueError(f"Expected one generic alt in {filename}, found {count}")
        path.write_text(updated, encoding="utf-8")

    css_path = root / "EPUB/styles/gaiden-premium.css"
    css = css_path.read_text(encoding="utf-8")
    old = "GAIDEN — PREMIUM REFLOWABLE EPUB THEME · RinoBooks / MantaQuest"
    new = "GAIDEN — PREMIUM REFLOWABLE EPUB THEME · RinoBooks / Wrecked Alien Machine"
    if css.count(old) != 1:
        raise ValueError("Canonical CSS comment not found exactly once")
    css_path.write_text(css.replace(old, new), encoding="utf-8")


def update_opf(root: Path) -> str:
    path = root / "EPUB/content.opf"
    ET.register_namespace("", OPF_NS)
    ET.register_namespace("dc", DC_NS)
    tree = ET.parse(path)
    package = tree.getroot()
    metadata = package.find(f"{{{OPF_NS}}}metadata")
    manifest = package.find(f"{{{OPF_NS}}}manifest")
    spine = package.find(f"{{{OPF_NS}}}spine")
    if metadata is None or manifest is None or spine is None:
        raise ValueError("OPF metadata, manifest, or spine missing")

    def dc(name: str, value: str) -> None:
        node = metadata.find(f"{{{DC_NS}}}{name}")
        if node is None:
            node = ET.SubElement(metadata, f"{{{DC_NS}}}{name}")
        node.text = value

    dc("title", "The Colour Out of Space")
    dc("creator", "H. P. Lovecraft")
    dc("language", "en-US")
    dc("publisher", "RinoBooks")
    dc("date", "2026")
    dc("rights", "Original work in the public domain. Modern English adaptation and original editorial material © 2026 RinoBooks.")
    modified = metadata.find(f"{{{OPF_NS}}}meta[@property='dcterms:modified']")
    if modified is None:
        modified = ET.SubElement(metadata, f"{{{OPF_NS}}}meta", {"property": "dcterms:modified"})
    modified.text = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    identifier_node = metadata.find(f"{{{DC_NS}}}identifier")
    if identifier_node is None or not (identifier_node.text or "").strip():
        raise ValueError("Existing UUID missing")
    identifier = (identifier_node.text or "").strip()

    ids_by_href = {
        item.get("href"): item.get("id")
        for item in manifest.findall(f"{{{OPF_NS}}}item")
    }
    missing = [href for href in SPINE if href not in ids_by_href]
    if missing:
        raise ValueError(f"Spine resources missing from manifest: {missing}")
    for itemref in list(spine):
        spine.remove(itemref)
    ncx_id = ids_by_href.get("toc.ncx")
    if not ncx_id:
        raise ValueError("NCX manifest item missing")
    spine.set("toc", ncx_id)
    for href in SPINE:
        ET.SubElement(spine, f"{{{OPF_NS}}}itemref", {"idref": str(ids_by_href[href])})

    tree.write(path, encoding="utf-8", xml_declaration=True)
    return identifier


def parse_links(path: Path, href_xpath: str, label_xpath: str, namespace: dict[str, str]) -> list[tuple[str, str]]:
    root = ET.parse(path).getroot()
    href_nodes = root.findall(href_xpath, namespace)
    label_nodes = root.findall(label_xpath, namespace)
    return [((label.text or "").strip(), href.get("href") or href.get("src") or "") for label, href in zip(label_nodes, href_nodes)]


def validate(root: Path, protected: dict[str, str]) -> None:
    for relative, digest in protected.items():
        if sha256(root / relative) != digest:
            raise ValueError(f"Protected content changed: {relative}")

    xml_files = [*root.rglob("*.xhtml"), *root.rglob("*.opf"), *root.rglob("*.ncx"), root / "META-INF/container.xml"]
    for path in xml_files:
        ET.parse(path)

    opf = ET.parse(root / "EPUB/content.opf").getroot()
    manifest = opf.find(f"{{{OPF_NS}}}manifest")
    spine = opf.find(f"{{{OPF_NS}}}spine")
    assert manifest is not None and spine is not None
    href_by_id = {item.get("id"): item.get("href") for item in manifest.findall(f"{{{OPF_NS}}}item")}
    for href in href_by_id.values():
        if not href or not (root / "EPUB" / href).is_file():
            raise ValueError(f"Manifest target missing: {href}")
    actual_spine = [href_by_id.get(item.get("idref")) for item in spine.findall(f"{{{OPF_NS}}}itemref")]
    if actual_spine != SPINE:
        raise ValueError(f"Unexpected spine: {actual_spine}")

    expected = [(label, f"{filename}#{section_id}") for label, filename, section_id in TOC]
    contents_root = ET.parse(root / "EPUB/text/contents.xhtml").getroot()
    contents = [
        ((a.text or "").strip(), a.get("href") or "")
        for a in contents_root.findall(".//x:a", {"x": XHTML_NS})
    ]
    nav_root = ET.parse(root / "EPUB/nav.xhtml").getroot()
    nav_toc = next(node for node in nav_root.findall(".//x:nav", {"x": XHTML_NS}) if node.get("id") == "toc")
    nav = [
        ((a.text or "").strip(), (a.get("href") or "").removeprefix("text/"))
        for a in nav_toc.findall(".//x:a", {"x": XHTML_NS})
    ]
    ncx_root = ET.parse(root / "EPUB/toc.ncx").getroot()
    ncx = [
        (
            (point.find(f"{{{NCX_NS}}}navLabel/{{{NCX_NS}}}text").text or "").strip(),
            (point.find(f"{{{NCX_NS}}}content").get("src") or "").removeprefix("text/"),
        )
        for point in ncx_root.findall(f".//{{{NCX_NS}}}navPoint")
    ]
    if contents != expected or nav != expected or ncx != expected:
        raise ValueError(f"Navigation mismatch\ncontents={contents}\nnav={nav}\nncx={ncx}")

    for base in (root / "EPUB/nav.xhtml", root / "EPUB/text/contents.xhtml", root / "EPUB/toc.ncx"):
        xml = ET.parse(base).getroot()
        for node in xml.iter():
            href = node.get("href") or node.get("src")
            if not href or href.startswith(("http:", "https:", "mailto:")):
                continue
            relative, _, fragment = href.partition("#")
            target = (base.parent / relative).resolve()
            if not target.is_file():
                raise ValueError(f"Broken link in {base.name}: {href}")
            if fragment:
                target_root = ET.parse(target).getroot()
                if not any(item.get("id") == fragment for item in target_root.iter()):
                    raise ValueError(f"Broken fragment in {base.name}: {href}")

    searchable = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".xhtml", ".opf", ".ncx", ".css"}
    )
    for term in PROHIBITED:
        if term in searchable:
            raise ValueError(f"Prohibited term remains: {term}")
    required = [
        "Wrecked Alien Machine",
        "Wrecked Alien Machines Editorial",
        "RinoBooks",
        "Péricles Guará Silva",
        "Modern English Adaptation",
        "The Colour Out of Space",
    ]
    for term in required:
        if term not in searchable:
            raise ValueError(f"Required term missing: {term}")


def package(root: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(f".{output.name}.tmp")
    if temp_output.exists():
        temp_output.unlink()
    with zipfile.ZipFile(temp_output, "w") as archive:
        mimetype = root / "mimetype"
        if mimetype.read_bytes() != b"application/epub+zip":
            raise ValueError("Invalid mimetype content")
        archive.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path == mimetype:
                continue
            archive.write(path, path.relative_to(root).as_posix(), compress_type=zipfile.ZIP_DEFLATED)
    os.replace(temp_output, output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        raise ValueError("Output must not overwrite input")

    with tempfile.TemporaryDirectory(prefix="gaiden-colour-v2-") as temp:
        root = Path(temp)
        with zipfile.ZipFile(args.input) as archive:
            archive.extractall(root)
        protected_paths = [
            *(f"EPUB/text/chapter_{index:03d}.xhtml" for index in range(1, 7)),
            "EPUB/images/cover.jpg",
            *(f"EPUB/images/{index:02d}.jpg" for index in range(1, 7)),
        ]
        protected = {relative: sha256(root / relative) for relative in protected_paths}
        calibre_bookmarks = root / "META-INF/calibre_bookmarks.txt"
        if calibre_bookmarks.exists():
            calibre_bookmarks.unlink()
        write_frontmatter(root)
        update_openings_and_css(root)
        identifier = update_opf(root)
        write_navigation(root, identifier)
        validate(root, protected)
        package(root, args.output)

    print(f"output={args.output.resolve()}")
    print(f"sha256={sha256(args.output)}")
    print("protected_chapters=unchanged")
    print("protected_images=unchanged")


if __name__ == "__main__":
    main()
