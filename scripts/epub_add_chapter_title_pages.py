#!/usr/bin/env python3
"""Create title, image, and narrative pages for every illustrated chapter."""

from __future__ import annotations

import argparse
import copy
import json
import tempfile
from pathlib import Path

from lxml import etree

from epub_split_chapter_openers import (
    NCX_NS,
    NS,
    OPF_NS,
    XHTML_NS,
    file_hashes,
    html_skeleton,
    metadata_signature,
    pack_epub,
    paragraph_texts,
    parse_xml,
    qname,
    run_epubcheck,
    safe_extract,
    sha256_file,
    validate_references,
    validate_zip,
    write_xhtml,
    write_xml,
)


CHAPTER_COUNT = 13
AUTHOR_NOTE_HREF = "text/authors_note.xhtml"

CSS_APPENDIX = """

/* Three-page chapter sequence: title, image, narrative. */
body.chapter-title-page,
body.chapter-image-page,
body.chapter-text-page {
  break-before: page;
  page-break-before: always;
}

body.chapter-title-page {
  box-sizing: border-box;
  height: 95vh;
  margin: 0;
  padding: 5%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.chapter-title-content {
  width: 100%;
  margin: 0;
  padding: 0;
  text-align: center;
}

.chapter-display-title {
  margin: 0;
  padding: 0;
  font-size: 2.4em;
  line-height: 1.2;
  font-weight: 700;
  text-align: center;
}

body.chapter-image-page {
  margin: 0;
  padding: 2.5vh 0;
}

.chapter-image-content,
.chapter-image-figure {
  margin: 0;
  padding: 0;
}

.chapter-image-content img {
  display: block;
  width: 100%;
  height: auto;
  max-height: 95vh;
  object-fit: contain;
  margin: 0 auto;
}
"""


def transform_title_and_create_image(text_dir: Path, number: int) -> dict[str, object]:
    title_path = text_dir / f"chapter_{number:02d}_title.xhtml"
    text_path = text_dir / f"chapter_{number:02d}_text.xhtml"
    image_path = text_dir / f"chapter_{number:02d}_image.xhtml"
    title_tree = parse_xml(title_path)
    text_tree = parse_xml(text_path)

    headings = title_tree.xpath("//x:h1", namespaces=NS)
    images = title_tree.xpath("//x:img", namespaces=NS)
    sections = title_tree.xpath("//x:body/x:section", namespaces=NS)
    if len(headings) != 1 or len(images) != 1 or len(sections) != 1:
        raise ValueError(
            f"Chapter {number}: expected one opener heading, image, and section; "
            f"got h1={len(headings)} image={len(images)} section={len(sections)}"
        )
    if text_tree.xpath("//x:h1 | //x:img", namespaces=NS):
        raise ValueError(f"Chapter {number}: narrative file contains a title or image")

    heading = headings[0]
    image = images[0]
    section = sections[0]
    heading_text = "".join(heading.itertext())
    heading_id = heading.get("id", "")
    if not heading_id:
        raise ValueError(f"Chapter {number}: title h1 has no id")

    image_container = image
    while image_container.getparent() is not section:
        parent = image_container.getparent()
        if parent is None:
            raise ValueError(f"Chapter {number}: image is outside opener section")
        image_container = parent

    image_tree, image_body = html_skeleton(heading_text, "chapter-image-page")
    image_section = etree.SubElement(image_body, qname(XHTML_NS, "section"))
    image_section.set("class", "chapter-image-content")
    copied_image = copy.deepcopy(image_container)
    if etree.QName(copied_image).localname == "figure":
        copied_image.set("class", "chapter-image-figure")
    image_section.append(copied_image)
    write_xhtml(image_tree, image_path)

    for child in list(section):
        if child is not heading:
            section.remove(child)
    section.set("class", "chapter-title-content")
    heading.set("class", "chapter-display-title")
    title_body = title_tree.xpath("//x:body", namespaces=NS)[0]
    title_body.set("class", "chapter-title-page")
    write_xhtml(title_tree, title_path)

    paragraphs = paragraph_texts(text_tree.getroot())
    if not paragraphs or any(text == "" for text in paragraphs):
        raise ValueError(f"Chapter {number}: narrative has missing or empty paragraphs")
    return {
        "chapter": number,
        "heading": heading_text,
        "anchor": heading_id,
        "title_file": f"text/{title_path.name}",
        "image_file": f"text/{image_path.name}",
        "text_file": f"text/{text_path.name}",
        "paragraph_count": len(paragraphs),
        "text_file_sha256": sha256_file(text_path),
        "narrative_sha256": __import__("hashlib").sha256("".join(paragraphs).encode("utf-8")).hexdigest(),
    }


def update_opf(opf_path: Path) -> None:
    tree = parse_xml(opf_path)
    root = tree.getroot()
    metadata_before = metadata_signature(root)
    manifest = root.find(qname(OPF_NS, "manifest"))
    spine = root.find(qname(OPF_NS, "spine"))
    if manifest is None or spine is None:
        raise ValueError("content.opf lacks manifest or spine")

    author_items = manifest.xpath(f"./opf:item[@href='{AUTHOR_NOTE_HREF}']", namespaces=NS)
    if len(author_items) != 1:
        raise ValueError(f"Expected one Author's Note manifest item; got {len(author_items)}")
    author_id = author_items[0].get("id", "")
    manifest.remove(author_items[0])
    author_refs = spine.xpath(f"./opf:itemref[@idref='{author_id}']", namespaces=NS)
    if len(author_refs) != 1:
        raise ValueError(f"Expected one Author's Note spine item; got {len(author_refs)}")
    spine.remove(author_refs[0])

    for number in range(1, CHAPTER_COUNT + 1):
        title_id = f"chapter_{number:02d}_title_xhtml"
        title_items = manifest.xpath(f"./opf:item[@id='{title_id}']", namespaces=NS)
        title_refs = spine.xpath(f"./opf:itemref[@idref='{title_id}']", namespaces=NS)
        if len(title_items) != 1 or len(title_refs) != 1:
            raise ValueError(f"Chapter {number}: missing title manifest/spine item")

        item = etree.Element(qname(OPF_NS, "item"))
        item.set("id", f"chapter_{number:02d}_image_xhtml")
        item.set("href", f"text/chapter_{number:02d}_image.xhtml")
        item.set("media-type", "application/xhtml+xml")
        manifest.insert(manifest.index(title_items[0]) + 1, item)

        itemref = etree.Element(qname(OPF_NS, "itemref"))
        itemref.set("idref", f"chapter_{number:02d}_image_xhtml")
        spine.insert(spine.index(title_refs[0]) + 1, itemref)

    if metadata_signature(root) != metadata_before:
        raise ValueError("Editorial metadata changed while updating content.opf")
    write_xml(tree, opf_path)


def remove_author_note_from_navigation(nav_path: Path, ncx_path: Path) -> None:
    nav_tree = parse_xml(nav_path)
    author_links = nav_tree.xpath(
        "//x:a[starts-with(@href, 'text/authors_note.xhtml')]", namespaces=NS
    )
    if len(author_links) != 1:
        raise ValueError(f"Expected one Author's Note NAV link; got {len(author_links)}")
    nav_li = author_links[0]
    while nav_li is not None and etree.QName(nav_li).localname != "li":
        nav_li = nav_li.getparent()
    if nav_li is None or nav_li.getparent() is None:
        raise ValueError("Could not remove Author's Note NAV list item")
    nav_li.getparent().remove(nav_li)
    write_xhtml(nav_tree, nav_path)

    ncx_tree = parse_xml(ncx_path)
    author_content = ncx_tree.xpath(
        "//ncx:content[starts-with(@src, 'text/authors_note.xhtml')]", namespaces=NS
    )
    if len(author_content) != 1:
        raise ValueError(f"Expected one Author's Note NCX target; got {len(author_content)}")
    nav_point = author_content[0]
    while nav_point is not None and etree.QName(nav_point).localname != "navPoint":
        nav_point = nav_point.getparent()
    if nav_point is None or nav_point.getparent() is None:
        raise ValueError("Could not remove Author's Note NCX navPoint")
    nav_point.getparent().remove(nav_point)
    for order, node in enumerate(ncx_tree.xpath("//ncx:navPoint", namespaces=NS), start=1):
        node.set("id", f"navPoint-{order}")
        node.set("playOrder", str(order))
    write_xml(ncx_tree, ncx_path)


def update_css(css_path: Path) -> None:
    current = css_path.read_text(encoding="utf-8")
    marker = "/* Three-page chapter sequence: title, image, narrative. */"
    if marker not in current:
        css_path.write_text(current.rstrip() + CSS_APPENDIX, encoding="utf-8")
    updated = css_path.read_text(encoding="utf-8")
    required = [
        "body.chapter-image-page",
        "font-size: 2.4em;",
        "font-weight: 700;",
        "break-before: page;",
        "page-break-before: always;",
        "object-fit: contain;",
        "max-height: 95vh;",
    ]
    missing = [value for value in required if value not in updated]
    if missing or "object-fit: cover" in updated:
        raise ValueError(f"CSS validation failed; missing={missing}")


def validate_package(
    root: Path,
    chapters: list[dict[str, object]],
    input_hashes: dict[str, str],
    metadata_before: list[dict[str, object]],
) -> dict[str, object]:
    epub_dir = root / "EPUB"
    text_dir = epub_dir / "text"
    opf_path = epub_dir / "content.opf"
    nav_path = epub_dir / "nav.xhtml"
    ncx_path = epub_dir / "toc.ncx"
    xml_paths = sorted(epub_dir.rglob("*.xhtml")) + [opf_path, ncx_path]
    for path in xml_paths:
        parse_xml(path)

    if (text_dir / "authors_note.xhtml").exists():
        raise ValueError("authors_note.xhtml still exists")
    for path in xml_paths:
        if "Author’s Note" in path.read_text(encoding="utf-8") or "authors_note.xhtml" in path.read_text(encoding="utf-8"):
            raise ValueError(f"Author's Note reference remains in {path}")

    for chapter in chapters:
        number = int(chapter["chapter"])
        title_tree = parse_xml(epub_dir / str(chapter["title_file"]))
        image_tree = parse_xml(epub_dir / str(chapter["image_file"]))
        text_tree = parse_xml(epub_dir / str(chapter["text_file"]))
        title_h1 = title_tree.xpath("//x:h1[@class='chapter-display-title']", namespaces=NS)
        if len(title_h1) != 1 or title_h1[0].get("id") != chapter["anchor"]:
            raise ValueError(f"Chapter {number}: invalid visible title h1")
        if title_tree.xpath("//x:img | //x:p | //x:br", namespaces=NS):
            raise ValueError(f"Chapter {number}: title page contains non-title content")
        if len(image_tree.xpath("//x:img", namespaces=NS)) != 1:
            raise ValueError(f"Chapter {number}: image page does not contain exactly one image")
        if image_tree.xpath("//x:h1 | //x:p | //x:br", namespaces=NS):
            raise ValueError(f"Chapter {number}: image page contains title or narrative content")
        if text_tree.xpath("//x:h1 | //x:img | //x:br", namespaces=NS):
            raise ValueError(f"Chapter {number}: narrative page contains title, image, or br")
        if text_tree.xpath("//x:p[not(normalize-space())]", namespaces=NS):
            raise ValueError(f"Chapter {number}: narrative contains empty paragraphs")
        rel_text = f"EPUB/{chapter['text_file']}"
        if input_hashes.get(rel_text) != sha256_file(epub_dir / str(chapter["text_file"])):
            raise ValueError(f"Chapter {number}: narrative XHTML bytes changed")

    opf_tree = parse_xml(opf_path)
    if metadata_signature(opf_tree.getroot()) != metadata_before:
        raise ValueError("Editorial metadata changed")
    manifest = {
        node.get("id"): node
        for node in opf_tree.xpath("//opf:manifest/opf:item", namespaces=NS)
    }
    spine = [node.get("idref") for node in opf_tree.xpath("//opf:spine/opf:itemref", namespaces=NS)]
    expected: list[str] = []
    for number in range(1, CHAPTER_COUNT + 1):
        expected.extend(
            [
                f"chapter_{number:02d}_title_xhtml",
                f"chapter_{number:02d}_image_xhtml",
                f"chapter_{number:02d}_text_xhtml",
            ]
        )
    actual = [item for item in spine if item and item.startswith("chapter_")]
    if actual != expected:
        raise ValueError(f"Chapter spine order is invalid: {actual}")
    if any(item not in manifest for item in spine):
        raise ValueError("Spine references an item absent from manifest")

    nav_tree = parse_xml(nav_path)
    ncx_tree = parse_xml(ncx_path)
    for chapter in chapters:
        target = f"{chapter['title_file']}#{chapter['anchor']}"
        if not nav_tree.xpath(f"//x:a[@href='{target}']", namespaces=NS):
            raise ValueError(f"NAV does not point to chapter {chapter['chapter']} title h1")
        if not ncx_tree.xpath(f"//ncx:content[@src='{target}']", namespaces=NS):
            raise ValueError(f"NCX does not point to chapter {chapter['chapter']} title h1")

    input_images = {
        name: digest for name, digest in input_hashes.items() if name.startswith("EPUB/media/")
    }
    output_images = {
        str(path.relative_to(root)): sha256_file(path)
        for path in sorted((epub_dir / "media").glob("*"))
        if path.is_file()
    }
    if input_images != output_images:
        raise ValueError("Images changed during transformation")

    refs = validate_references(root, xml_paths)
    return {
        "xml_files_validated": len(xml_paths),
        "xhtml_files_validated": len(list(epub_dir.rglob("*.xhtml"))),
        "content_opf_valid": True,
        "nav_xhtml_valid": True,
        "toc_ncx_valid": True,
        "authors_note_removed": True,
        "chapter_spine_sequence_valid": True,
        "images_unchanged": True,
        "narrative_xhtml_bytes_unchanged": True,
        **refs,
    }


def transform(input_epub: Path, output_epub: Path, report_path: Path) -> dict[str, object]:
    input_epub = input_epub.resolve()
    output_epub = output_epub.resolve()
    if input_epub == output_epub:
        raise ValueError("Input and output paths must differ")

    with tempfile.TemporaryDirectory(prefix="gaiden_epub_title_pages_") as temp_name:
        root = Path(temp_name)
        safe_extract(input_epub, root)
        before_hashes = file_hashes(root)
        metadata_before = metadata_signature(parse_xml(root / "EPUB/content.opf").getroot())
        text_dir = root / "EPUB/text"
        chapters = [
            transform_title_and_create_image(text_dir, number)
            for number in range(1, CHAPTER_COUNT + 1)
        ]
        update_opf(root / "EPUB/content.opf")
        remove_author_note_from_navigation(root / "EPUB/nav.xhtml", root / "EPUB/toc.ncx")
        update_css(root / "EPUB/styles/stylesheet1.css")
        (text_dir / "authors_note.xhtml").unlink()

        validation = validate_package(root, chapters, before_hashes, metadata_before)
        after_hashes = file_hashes(root)
        created = sorted(set(after_hashes) - set(before_hashes))
        removed = sorted(set(before_hashes) - set(after_hashes))
        altered = sorted(
            name for name in set(before_hashes) & set(after_hashes) if before_hashes[name] != after_hashes[name]
        )
        pack_epub(root, output_epub)

    zip_validation = validate_zip(output_epub)
    epubcheck_result = run_epubcheck(output_epub)
    total_paragraphs = sum(int(chapter["paragraph_count"]) for chapter in chapters)
    report = {
        "schema": "gaiden_epub_three_page_chapters_v1",
        "input_epub": str(input_epub),
        "output_epub": str(output_epub),
        "input_sha256": sha256_file(input_epub),
        "output_sha256": sha256_file(output_epub),
        "chapter_count": len(chapters),
        "chapter_page_sequence": ["title", "image", "text"],
        "paragraph_count_before": total_paragraphs,
        "paragraph_count_after": total_paragraphs,
        "narrative_text_integral_equality": True,
        "editorial_metadata_unchanged": True,
        "authors_note_removed": True,
        "chapters": chapters,
        "files": {
            "created": created,
            "removed": removed,
            "altered": altered,
            "unchanged_count": len(set(before_hashes) & set(after_hashes)) - len(altered),
        },
        "package_validation": validation,
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
