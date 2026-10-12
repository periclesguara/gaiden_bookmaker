#!/usr/bin/env python3
"""Split illustrated EPUB chapters into opener and narrative XHTML files.

The transformation is deliberately strict: it aborts unless every chapter has
exactly one heading, exactly one image, and byte-for-byte equivalent narrative
text (grouped by paragraph) after the split.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import posixpath
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from lxml import etree


XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"
OPF_NS = "http://www.idpf.org/2007/opf"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NS = {"x": XHTML_NS, "opf": OPF_NS, "ncx": NCX_NS}
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False)

CHAPTER_SOURCES = [f"ch{number:03d}.xhtml" for number in range(4, 17)]

CSS_APPENDIX = """

/* Dedicated illustrated chapter openers. */
body.chapter-title-page,
body.chapter-text-page {
  break-before: page;
  page-break-before: always;
}

.visually-hidden {
  position: absolute !important;
  width: 1px !important;
  height: 1px !important;
  padding: 0 !important;
  margin: -1px !important;
  overflow: hidden !important;
  clip: rect(0, 0, 0, 0) !important;
  white-space: nowrap !important;
  border: 0 !important;
}

body.chapter-title-page {
  margin: 0;
  padding: 2.5vh 0;
}

.chapter-opener,
.chapter-opener-figure {
  margin: 0;
  padding: 0;
}

.chapter-opener img {
  display: block;
  width: 100%;
  height: auto;
  max-height: 95vh;
  object-fit: contain;
  margin: 0 auto;
}

body.chapter-text-page {
  line-height: 1.45;
  orphans: 2;
  widows: 2;
}

.chapter-text-content > p {
  text-indent: 1.2em !important;
}

.chapter-text-content > p:first-of-type {
  text-indent: 0 !important;
}
"""


def qname(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_xml(path: Path) -> etree._ElementTree:
    return etree.parse(str(path), PARSER)


def write_xhtml(tree: etree._ElementTree, path: Path) -> None:
    tree.write(
        str(path),
        encoding="UTF-8",
        xml_declaration=True,
        doctype="<!DOCTYPE html>",
        pretty_print=True,
    )


def write_xml(tree: etree._ElementTree, path: Path) -> None:
    tree.write(str(path), encoding="UTF-8", xml_declaration=True, pretty_print=True)


def paragraph_texts(root: etree._Element) -> list[str]:
    return ["".join(node.itertext()) for node in root.xpath("//x:p", namespaces=NS)]


def metadata_signature(opf_root: etree._Element) -> list[dict[str, object]]:
    metadata = opf_root.find(qname(OPF_NS, "metadata"))
    if metadata is None:
        raise ValueError("content.opf has no metadata element")
    signature: list[dict[str, object]] = []
    for node in metadata.iter():
        signature.append(
            {
                "tag": node.tag,
                "attributes": sorted(node.attrib.items()),
                "text": node.text or "",
                "tail": node.tail or "",
            }
        )
    return signature


def html_skeleton(title: str, body_class: str) -> tuple[etree._ElementTree, etree._Element]:
    html = etree.Element(
        qname(XHTML_NS, "html"),
        nsmap={None: XHTML_NS, "epub": EPUB_NS},
    )
    html.set("lang", "en")
    html.set(qname(XML_NS, "lang"), "en")
    head = etree.SubElement(html, qname(XHTML_NS, "head"))
    meta = etree.SubElement(head, qname(XHTML_NS, "meta"))
    meta.set("charset", "utf-8")
    title_node = etree.SubElement(head, qname(XHTML_NS, "title"))
    title_node.text = title
    link = etree.SubElement(head, qname(XHTML_NS, "link"))
    link.set("rel", "stylesheet")
    link.set("type", "text/css")
    link.set("href", "../styles/stylesheet1.css")
    body = etree.SubElement(html, qname(XHTML_NS, "body"))
    body.set(qname(EPUB_NS, "type"), "bodymatter")
    body.set("class", body_class)
    return etree.ElementTree(html), body


def top_level_child(section: etree._Element, node: etree._Element) -> etree._Element:
    current = node
    while current.getparent() is not section:
        parent = current.getparent()
        if parent is None:
            raise ValueError("Chapter image is outside the chapter section")
        current = parent
    return current


def split_chapter(text_dir: Path, source_name: str, chapter_number: int) -> dict[str, object]:
    source_path = text_dir / source_name
    source_tree = parse_xml(source_path)
    source_root = source_tree.getroot()
    sections = source_root.xpath("//x:body/x:section", namespaces=NS)
    headings = source_root.xpath("//x:h1", namespaces=NS)
    images = source_root.xpath("//x:img", namespaces=NS)
    if len(sections) != 1 or len(headings) != 1 or len(images) != 1:
        raise ValueError(
            f"{source_name}: expected one section, h1, and image; got "
            f"section={len(sections)} h1={len(headings)} image={len(images)}"
        )

    section = sections[0]
    heading = headings[0]
    image = images[0]
    heading_text = "".join(heading.itertext())
    heading_id = (heading.get("id") or section.get("id") or "").strip()
    if not heading_id:
        raise ValueError(f"{source_name}: chapter heading has no usable anchor id")

    before_paragraphs = paragraph_texts(source_root)
    if not before_paragraphs or any(text == "" for text in before_paragraphs):
        raise ValueError(f"{source_name}: missing or empty narrative paragraph")

    title_tree, title_body = html_skeleton(heading_text, "chapter-title-page")
    opener = etree.SubElement(title_body, qname(XHTML_NS, "section"))
    opener.set("class", "chapter-opener")
    hidden_heading = etree.SubElement(opener, qname(XHTML_NS, "h1"))
    hidden_heading.set("id", heading_id)
    hidden_heading.set("class", "visually-hidden")
    hidden_heading.text = heading_text
    source_image_container = top_level_child(section, image)
    opener_image_container = copy.deepcopy(source_image_container)
    if etree.QName(opener_image_container).localname == "figure":
        classes = opener_image_container.get("class", "").split()
        if "chapter-opener-figure" not in classes:
            classes.append("chapter-opener-figure")
        opener_image_container.set("class", " ".join(filter(None, classes)))
    opener.append(opener_image_container)

    text_tree, text_body = html_skeleton(heading_text, "chapter-text-page")
    narrative = etree.SubElement(text_body, qname(XHTML_NS, "section"))
    narrative.set("class", "chapter-text-content")
    excluded = {heading, source_image_container}
    for child in section:
        if child not in excluded:
            narrative.append(copy.deepcopy(child))

    after_paragraphs = paragraph_texts(text_tree.getroot())
    if before_paragraphs != after_paragraphs:
        raise ValueError(f"{source_name}: narrative text changed during chapter split")
    if text_tree.xpath("//x:h1 | //x:img", namespaces=NS):
        raise ValueError(f"{source_name}: title or image leaked into narrative XHTML")
    if title_tree.xpath("//x:p", namespaces=NS):
        raise ValueError(f"{source_name}: narrative paragraph leaked into opener XHTML")

    title_name = f"chapter_{chapter_number:02d}_title.xhtml"
    text_name = f"chapter_{chapter_number:02d}_text.xhtml"
    write_xhtml(title_tree, text_dir / title_name)
    write_xhtml(text_tree, text_dir / text_name)

    return {
        "chapter": chapter_number,
        "source": f"text/{source_name}",
        "title_file": f"text/{title_name}",
        "text_file": f"text/{text_name}",
        "heading": heading_text,
        "anchor": heading_id,
        "image_src": image.get("src", ""),
        "paragraph_count_before": len(before_paragraphs),
        "paragraph_count_after": len(after_paragraphs),
        "narrative_sha256_before": sha256_bytes("".join(before_paragraphs).encode("utf-8")),
        "narrative_sha256_after": sha256_bytes("".join(after_paragraphs).encode("utf-8")),
        "narrative_text_equal": before_paragraphs == after_paragraphs,
    }


def replace_opf_chapters(opf_path: Path, chapters: list[dict[str, object]]) -> None:
    tree = parse_xml(opf_path)
    root = tree.getroot()
    metadata_before = metadata_signature(root)
    manifest = root.find(qname(OPF_NS, "manifest"))
    spine = root.find(qname(OPF_NS, "spine"))
    if manifest is None or spine is None:
        raise ValueError("content.opf lacks manifest or spine")

    for chapter, source_name in zip(chapters, CHAPTER_SOURCES, strict=True):
        old_id = f"{Path(source_name).stem}_xhtml"
        old_item = manifest.xpath(f"./opf:item[@id='{old_id}']", namespaces=NS)
        old_ref = spine.xpath(f"./opf:itemref[@idref='{old_id}']", namespaces=NS)
        if len(old_item) != 1 or len(old_ref) != 1:
            raise ValueError(f"content.opf: expected one manifest/spine entry for {old_id}")

        manifest_index = manifest.index(old_item[0])
        spine_index = spine.index(old_ref[0])
        manifest.remove(old_item[0])
        spine.remove(old_ref[0])

        for offset, kind in enumerate(("title", "text")):
            item = etree.Element(qname(OPF_NS, "item"))
            item.set("id", f"chapter_{int(chapter['chapter']):02d}_{kind}_xhtml")
            item.set("href", f"text/chapter_{int(chapter['chapter']):02d}_{kind}.xhtml")
            item.set("media-type", "application/xhtml+xml")
            manifest.insert(manifest_index + offset, item)

            itemref = etree.Element(qname(OPF_NS, "itemref"))
            itemref.set("idref", f"chapter_{int(chapter['chapter']):02d}_{kind}_xhtml")
            spine.insert(spine_index + offset, itemref)

    if metadata_signature(root) != metadata_before:
        raise ValueError("Editorial metadata changed while updating content.opf")
    write_xml(tree, opf_path)


def replace_navigation(nav_path: Path, ncx_path: Path, chapters: list[dict[str, object]]) -> None:
    nav_tree = parse_xml(nav_path)
    ncx_tree = parse_xml(ncx_path)
    for chapter, source_name in zip(chapters, CHAPTER_SOURCES, strict=True):
        old_prefix = f"text/{source_name}#"
        new_href = f"{chapter['title_file']}#{chapter['anchor']}"
        nav_matches = [
            node
            for node in nav_tree.xpath("//x:a[@href]", namespaces=NS)
            if node.get("href", "").startswith(old_prefix)
        ]
        if not nav_matches:
            raise ValueError(f"nav.xhtml has no link for {source_name}")
        for node in nav_matches:
            node.set("href", new_href)

        ncx_matches = [
            node
            for node in ncx_tree.xpath("//ncx:content[@src]", namespaces=NS)
            if node.get("src", "").startswith(old_prefix)
        ]
        if len(ncx_matches) != 1:
            raise ValueError(f"toc.ncx expected one link for {source_name}; got {len(ncx_matches)}")
        ncx_matches[0].set("src", new_href)

    write_xhtml(nav_tree, nav_path)
    write_xml(ncx_tree, ncx_path)


def append_css(css_path: Path) -> None:
    current = css_path.read_text(encoding="utf-8")
    if "object-fit: cover" in current:
        raise ValueError("Existing CSS contains forbidden object-fit: cover")
    marker = "/* Dedicated illustrated chapter openers. */"
    if marker not in current:
        css_path.write_text(current.rstrip() + CSS_APPENDIX, encoding="utf-8")
    updated = css_path.read_text(encoding="utf-8")
    required = [
        "break-before: page;",
        "page-break-before: always;",
        "display: block;",
        "width: 100%;",
        "height: auto;",
        "max-height: 95vh;",
        "object-fit: contain;",
        "margin: 0 auto;",
        "line-height: 1.45;",
        "orphans: 2;",
        "widows: 2;",
        "text-indent: 1.2em !important;",
        "text-indent: 0 !important;",
    ]
    missing = [declaration for declaration in required if declaration not in updated]
    if missing or "object-fit: cover" in updated:
        raise ValueError(f"CSS validation failed; missing={missing}")


def local_target(root: Path, source_file: Path, reference: str) -> tuple[Path, str]:
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc:
        return Path(), ""
    raw_path = unquote(parsed.path)
    target = source_file if not raw_path else (source_file.parent / raw_path).resolve()
    root_resolved = root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"Reference escapes EPUB root: {source_file}: {reference}") from exc
    return target, unquote(parsed.fragment)


def validate_references(root: Path, xml_paths: list[Path]) -> dict[str, int]:
    checked_files = 0
    checked_anchors = 0
    id_cache: dict[Path, set[str]] = {}
    for source_path in xml_paths:
        tree = parse_xml(source_path)
        for node in tree.getroot().iter():
            for attr_name, reference in node.attrib.items():
                if etree.QName(attr_name).localname not in {"href", "src"}:
                    continue
                target, fragment = local_target(root, source_path.resolve(), reference)
                if not str(target):
                    continue
                if not target.exists() or not target.is_file():
                    raise ValueError(f"Broken reference: {source_path}: {reference}")
                checked_files += 1
                if fragment:
                    if target not in id_cache:
                        target_tree = parse_xml(target)
                        ids = {
                            value
                            for element in target_tree.getroot().iter()
                            for value in (element.get("id"), element.get(qname(XML_NS, "id")))
                            if value
                        }
                        id_cache[target] = ids
                    if fragment not in id_cache[target]:
                        raise ValueError(f"Broken anchor: {source_path}: {reference}")
                    checked_anchors += 1
    return {"file_references_checked": checked_files, "anchors_checked": checked_anchors}


def validate_package(root: Path, chapters: list[dict[str, object]], image_hashes_before: dict[str, str]) -> dict[str, object]:
    epub_dir = root / "EPUB"
    text_dir = epub_dir / "text"
    opf_path = epub_dir / "content.opf"
    nav_path = epub_dir / "nav.xhtml"
    ncx_path = epub_dir / "toc.ncx"
    xml_paths = sorted(epub_dir.rglob("*.xhtml")) + [opf_path, ncx_path]
    for path in xml_paths:
        parse_xml(path)

    if len(chapters) != 13:
        raise ValueError(f"Expected 13 chapters, got {len(chapters)}")
    for chapter in chapters:
        title_path = epub_dir / str(chapter["title_file"])
        text_path = epub_dir / str(chapter["text_file"])
        title_tree = parse_xml(title_path)
        text_tree = parse_xml(text_path)
        hidden = title_tree.xpath("//x:h1[@class='visually-hidden']", namespaces=NS)
        images = title_tree.xpath("//x:img", namespaces=NS)
        if len(hidden) != 1 or hidden[0].get("id") != chapter["anchor"] or len(images) != 1:
            raise ValueError(f"Chapter {chapter['chapter']} opener semantics are invalid")
        if title_tree.xpath("//x:p", namespaces=NS):
            raise ValueError(f"Chapter {chapter['chapter']} opener contains narrative")
        if text_tree.xpath("//x:h1 | //x:img", namespaces=NS):
            raise ValueError(f"Chapter {chapter['chapter']} text duplicates title/image")
        if text_tree.xpath("//x:p[not(normalize-space())]", namespaces=NS):
            raise ValueError(f"Chapter {chapter['chapter']} text contains empty paragraphs")
        if title_tree.xpath("//x:br[following-sibling::*[1][self::x:br]]", namespaces=NS):
            raise ValueError(f"Chapter {chapter['chapter']} opener uses redundant br elements")
        if chapter["paragraph_count_before"] != chapter["paragraph_count_after"]:
            raise ValueError(f"Chapter {chapter['chapter']} paragraph count changed")
        if chapter["narrative_sha256_before"] != chapter["narrative_sha256_after"]:
            raise ValueError(f"Chapter {chapter['chapter']} narrative hash changed")

    opf_tree = parse_xml(opf_path)
    manifest_items = opf_tree.xpath("//opf:manifest/opf:item", namespaces=NS)
    manifest_ids = {item.get("id"): item for item in manifest_items}
    spine_ids = [item.get("idref") for item in opf_tree.xpath("//opf:spine/opf:itemref", namespaces=NS)]
    expected_sequence: list[str] = []
    for number in range(1, 14):
        expected_sequence.extend(
            [f"chapter_{number:02d}_title_xhtml", f"chapter_{number:02d}_text_xhtml"]
        )
    chapter_spine = [item_id for item_id in spine_ids if item_id and item_id.startswith("chapter_")]
    if chapter_spine != expected_sequence:
        raise ValueError(f"Chapter spine order is invalid: {chapter_spine}")
    if any(item_id not in manifest_ids for item_id in spine_ids):
        raise ValueError("Spine references an item absent from the manifest")

    image_hashes_after = {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted((epub_dir / "media").glob("*"))
        if path.is_file()
    }
    if image_hashes_after != image_hashes_before:
        raise ValueError("One or more images changed during transformation")

    refs = validate_references(root, xml_paths)
    return {
        "xml_files_validated": len(xml_paths),
        "xhtml_files_validated": len(list(epub_dir.rglob("*.xhtml"))),
        "content_opf_valid": True,
        "nav_xhtml_valid": True,
        "toc_ncx_valid": True,
        "manifest_items": len(manifest_items),
        "spine_items": len(spine_ids),
        "chapter_spine_sequence_valid": True,
        "images_unchanged": True,
        **refs,
    }


def safe_extract(epub_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(epub_path) as archive:
        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            try:
                target.relative_to(destination.resolve())
            except ValueError as exc:
                raise ValueError(f"Unsafe ZIP member: {info.filename}") from exc
        archive.extractall(destination)


def pack_epub(root: Path, output: Path) -> None:
    mimetype = root / "mimetype"
    if mimetype.read_bytes() != b"application/epub+zip":
        raise ValueError("Invalid EPUB mimetype content")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as archive:
        archive.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path == mimetype:
                continue
            archive.write(
                path,
                path.relative_to(root).as_posix(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )


def validate_zip(output: Path) -> dict[str, object]:
    with zipfile.ZipFile(output) as archive:
        infos = archive.infolist()
        if not infos or infos[0].filename != "mimetype":
            raise ValueError("mimetype is not the first ZIP entry")
        if infos[0].compress_type != zipfile.ZIP_STORED:
            raise ValueError("mimetype is compressed")
        if archive.read("mimetype") != b"application/epub+zip":
            raise ValueError("mimetype content is invalid")
        bad = archive.testzip()
        if bad:
            raise ValueError(f"Corrupt ZIP member: {bad}")
        return {
            "zip_entries": len(infos),
            "mimetype_first": True,
            "mimetype_uncompressed": True,
            "zip_integrity_valid": True,
        }


def file_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def run_epubcheck(output: Path) -> dict[str, object]:
    executable = shutil.which("epubcheck")
    if not executable:
        raise RuntimeError("epubcheck is required but was not found")
    result = subprocess.run(
        [executable, str(output)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"EPUBCheck failed:\n{result.stdout}")
    return {"passed": True, "returncode": result.returncode, "output": result.stdout.strip()}


def transform(input_epub: Path, output_epub: Path, report_path: Path) -> dict[str, object]:
    input_epub = input_epub.resolve()
    output_epub = output_epub.resolve()
    if input_epub == output_epub:
        raise ValueError("Input and output EPUB paths must differ")

    with tempfile.TemporaryDirectory(prefix="gaiden_epub_split_") as temp_name:
        root = Path(temp_name)
        safe_extract(input_epub, root)
        before_hashes = file_hashes(root)
        image_hashes_before = {
            name: digest for name, digest in before_hashes.items() if name.startswith("EPUB/media/")
        }
        opf_before = parse_xml(root / "EPUB/content.opf")
        metadata_before = metadata_signature(opf_before.getroot())

        text_dir = root / "EPUB/text"
        chapters = [
            split_chapter(text_dir, source_name, number)
            for number, source_name in enumerate(CHAPTER_SOURCES, start=1)
        ]
        replace_opf_chapters(root / "EPUB/content.opf", chapters)
        replace_navigation(root / "EPUB/nav.xhtml", root / "EPUB/toc.ncx", chapters)
        append_css(root / "EPUB/styles/stylesheet1.css")
        for source_name in CHAPTER_SOURCES:
            (text_dir / source_name).unlink()

        metadata_after = metadata_signature(parse_xml(root / "EPUB/content.opf").getroot())
        if metadata_before != metadata_after:
            raise ValueError("Editorial metadata differs after transformation")

        package_validation = validate_package(root, chapters, image_hashes_before)
        after_hashes = file_hashes(root)
        created = sorted(set(after_hashes) - set(before_hashes))
        removed = sorted(set(before_hashes) - set(after_hashes))
        altered = sorted(
            name for name in set(before_hashes) & set(after_hashes) if before_hashes[name] != after_hashes[name]
        )

        expected_created = sorted(
            f"EPUB/text/chapter_{number:02d}_{kind}.xhtml"
            for number in range(1, 14)
            for kind in ("title", "text")
        )
        expected_removed = sorted(f"EPUB/text/{name}" for name in CHAPTER_SOURCES)
        if created != expected_created or removed != expected_removed:
            raise ValueError(f"Unexpected file delta: created={created}, removed={removed}")

        pack_epub(root, output_epub)

    zip_validation = validate_zip(output_epub)
    epubcheck_result = run_epubcheck(output_epub)
    total_before = sum(int(chapter["paragraph_count_before"]) for chapter in chapters)
    total_after = sum(int(chapter["paragraph_count_after"]) for chapter in chapters)
    report = {
        "schema": "gaiden_epub_chapter_openers_v1",
        "input_epub": str(input_epub),
        "output_epub": str(output_epub),
        "input_sha256": sha256_file(input_epub),
        "output_sha256": sha256_file(output_epub),
        "chapter_count_before": len(CHAPTER_SOURCES),
        "chapter_count_after": len(chapters),
        "paragraph_count_before": total_before,
        "paragraph_count_after": total_after,
        "paragraph_count_equal": total_before == total_after,
        "narrative_text_integral_equality": all(bool(chapter["narrative_text_equal"]) for chapter in chapters),
        "editorial_metadata_unchanged": metadata_before == metadata_after,
        "chapters": chapters,
        "files": {
            "created": created,
            "removed": removed,
            "altered": altered,
            "unchanged_count": len(set(before_hashes) & set(after_hashes)) - len(altered),
        },
        "package_validation": package_validation,
        "zip_validation": zip_validation,
        "epubcheck": epubcheck_result,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_epub", type=Path)
    parser.add_argument("output_epub", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = transform(args.input_epub, args.output_epub, args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
