#!/usr/bin/env python3
"""Rename Republic summary page to Introduction and export v05 FINAL EPUB."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[2]
INPUT_EPUB = ROOT / "data/builds/book_0027/en/republic_of_plato_v04.epub"
OUTPUT_EPUB = ROOT / "data/builds/book_0027/en/republic_of_plato_v05_FINAL.epub"
REPORT_PATH = ROOT / "data/builds/book_0027/en/republic_of_plato_v05_FINAL_report.json"

OLD_FILE = "EPUB/text/summary_and_central_argument.xhtml"
NEW_FILE = "EPUB/text/introduction.xhtml"
OLD_HREF = "text/summary_and_central_argument.xhtml"
NEW_HREF = "text/introduction.xhtml"
INTRO_ID = "introduction"
INTRO_LABEL = "Introduction"
INTRO_SUBTITLE = "The Republic as a Drama of Justice, Power, Education, and the Soul"
GLOSSARY_LABEL = "Glossary of Key Terms and Names"

SCAN_TERMS = [
    "Summary and Central Argument",
    "Adapted Preface",
    "Modern en Edition",
    "Rinobooks",
    ":::",
    "Markdown Preview",
    "Back to Editions",
    "/home/",
    "Book 02 - begins",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def rename_intro_file(workdir: Path) -> bool:
    old_path = workdir / OLD_FILE
    new_path = workdir / NEW_FILE
    if not old_path.exists() and new_path.exists():
        return False
    if not old_path.exists():
        raise FileNotFoundError(old_path)

    soup = BeautifulSoup(old_path.read_text(encoding="utf-8"), "xml")
    title = soup.find("title")
    if title is not None:
        title.string = INTRO_LABEL
    h1 = soup.find("h1")
    if h1 is not None:
        h1["id"] = INTRO_ID
        h1.string = INTRO_LABEL
    else:
        body = soup.find("body")
        h1 = soup.new_tag("h1")
        h1["id"] = INTRO_ID
        h1.string = INTRO_LABEL
        if body is not None:
            body.insert(0, h1)

    if not soup.find(["h2", "p"], string=INTRO_SUBTITLE):
        subtitle = soup.new_tag("h2")
        subtitle["class"] = "section-subtitle"
        subtitle.string = INTRO_SUBTITLE
        h1.insert_after(subtitle)

    new_path.write_text(str(soup), encoding="utf-8")
    old_path.unlink()
    return True


def update_opf(workdir: Path) -> dict[str, object]:
    path = workdir / "EPUB/content.opf"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
    manifest = soup.find("manifest")
    spine = soup.find("spine")
    metadata = soup.find("metadata")
    if manifest is None or spine is None:
        raise ValueError("OPF manifest/spine not found")

    old_item = soup.find("item", href=OLD_HREF)
    if old_item is None:
        old_item = soup.find("item", id="text_summary_and_central_argument_xhtml")
    if old_item is None:
        intro_item = soup.find("item", href=NEW_HREF)
        if intro_item is None:
            intro_item = soup.new_tag("item")
            manifest.append(intro_item)
        intro_item["id"] = "text_introduction_xhtml"
        intro_item["href"] = NEW_HREF
        intro_item["media-type"] = "application/xhtml+xml"
    else:
        old_item["id"] = "text_introduction_xhtml"
        old_item["href"] = NEW_HREF
        old_item["media-type"] = "application/xhtml+xml"

    for item in list(soup.find_all("item")):
        if item.get("href") == OLD_HREF or item.get("id") == "text_summary_and_central_argument_xhtml":
            if item.get("id") != "text_introduction_xhtml":
                item.extract()

    for itemref in soup.find_all("itemref"):
        if itemref.get("idref") == "text_summary_and_central_argument_xhtml":
            itemref["idref"] = "text_introduction_xhtml"

    nav_ref = soup.find("itemref", idref="nav")
    if nav_ref is not None:
        nav_ref["linear"] = "no"

    # Put Epilogue before Glossary, and keep both after Book 10.
    epilogue_ref = soup.find("itemref", idref="text_epilogue_xhtml")
    glossary_ref = soup.find("itemref", idref="text_glossary_xhtml")
    book10_ref = soup.find("itemref", idref="text_book_10_xhtml")
    if epilogue_ref is not None:
        epilogue_ref.extract()
    if glossary_ref is not None:
        glossary_ref.extract()
    if book10_ref is not None:
        if epilogue_ref is not None:
            book10_ref.insert_after(epilogue_ref)
        if glossary_ref is not None:
            (epilogue_ref or book10_ref).insert_after(glossary_ref)
    else:
        if epilogue_ref is not None:
            spine.append(epilogue_ref)
        if glossary_ref is not None:
            spine.append(glossary_ref)

    if metadata is not None:
        modified = metadata.find("meta", attrs={"property": "dcterms:modified"})
        if modified is None:
            modified = soup.new_tag("meta")
            modified["property"] = "dcterms:modified"
            metadata.append(modified)
        modified.string = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    path.write_text(str(soup), encoding="utf-8")

    manifest_ids = [item.get("id") for item in soup.find_all("item")]
    spine_refs = [(itemref.get("idref"), itemref.get("linear")) for itemref in soup.find_all("itemref")]
    return {"manifest_ids": manifest_ids, "spine_refs": spine_refs}


def update_nav(workdir: Path) -> bool:
    path = workdir / "EPUB/nav.xhtml"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
    toc = soup.find("nav", {"epub:type": "toc"}) or soup.find("nav", id="toc")
    if toc is None:
        return False
    ol = toc.find("ol")
    if ol is None:
        return False

    for link in soup.find_all("a"):
        href = str(link.get("href", ""))
        text = link.get_text(" ", strip=True)
        if OLD_HREF in href or text == "Summary and Central Argument":
            link["href"] = "text/introduction.xhtml#introduction"
            link.string = INTRO_LABEL

    def extract_li_by_href(href: str):
        link = soup.find("a", href=href)
        if link is None:
            return None
        li = link.find_parent("li")
        if li is not None:
            li.extract()
        return li

    epilogue_li = extract_li_by_href("text/epilogue.xhtml#epilogue")
    glossary_li = extract_li_by_href("text/glossary.xhtml#glossary")
    if epilogue_li is None:
        epilogue_li = soup.new_tag("li")
        a = soup.new_tag("a")
        a["href"] = "text/epilogue.xhtml#epilogue"
        a.string = "Epilogue"
        epilogue_li.append(a)
    if glossary_li is None:
        glossary_li = soup.new_tag("li")
        a = soup.new_tag("a")
        a["href"] = "text/glossary.xhtml#glossary"
        a.string = GLOSSARY_LABEL
        glossary_li.append(a)
    ol.append(epilogue_li)
    ol.append(glossary_li)

    path.write_text(str(soup), encoding="utf-8")
    return True


def update_ncx(workdir: Path) -> bool:
    path = workdir / "EPUB/toc.ncx"
    if not path.exists():
        return False
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
    nav_map = soup.find("navMap")
    if nav_map is None:
        return False

    for point in soup.find_all("navPoint"):
        label = point.find("navLabel")
        text_node = label.find("text") if label else None
        content = point.find("content")
        label_text = text_node.get_text(" ", strip=True) if text_node else ""
        src = str(content.get("src", "")) if content else ""
        if OLD_HREF in src or label_text == "Summary and Central Argument":
            if text_node is not None:
                text_node.string = INTRO_LABEL
            if content is not None:
                content["src"] = "text/introduction.xhtml#introduction"

    def extract_navpoint_by_src(src: str):
        content = soup.find("content", src=src)
        if content is None:
            return None
        point = content.find_parent("navPoint")
        if point is not None:
            point.extract()
        return point

    epilogue = extract_navpoint_by_src("text/epilogue.xhtml#epilogue")
    glossary = extract_navpoint_by_src("text/glossary.xhtml#glossary")
    if epilogue is not None:
        nav_map.append(epilogue)
    if glossary is not None:
        nav_map.append(glossary)

    for idx, point in enumerate(soup.find_all("navPoint"), start=1):
        point["playOrder"] = str(idx)
        point["id"] = f"navPoint-{idx}"

    path.write_text(str(soup), encoding="utf-8")
    return True


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


def validate(workdir: Path) -> dict[str, object]:
    ids_by_file, duplicate_ids = collect_ids(workdir)
    existing = {path.relative_to(workdir).as_posix() for path in workdir.rglob("*") if path.is_file()}
    broken_links: list[str] = []
    parse_errors: list[str] = []

    for path in sorted((workdir / "EPUB").rglob("*.xhtml")):
        rel = path.relative_to(workdir).as_posix()
        try:
            soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
        except Exception as exc:
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
    manifest = {item.get("id"): item.get("href") for item in opf.find_all("item")}
    spine_refs = [itemref.get("idref") for itemref in opf.find_all("itemref")]
    spine_hrefs = [manifest.get(ref, "") for ref in spine_refs]
    nav_ref = opf.find("itemref", idref="nav")

    nav = BeautifulSoup((workdir / "EPUB/nav.xhtml").read_text(encoding="utf-8"), "xml")
    toc_links = [
        (a.get_text(" ", strip=True), a.get("href"))
        for a in (nav.find("nav", id="toc") or nav).find_all("a")
    ]
    ncx = BeautifulSoup((workdir / "EPUB/toc.ncx").read_text(encoding="utf-8"), "xml")
    ncx_labels = [label.get_text(" ", strip=True) for label in ncx.find_all("navLabel")]

    intro_count = sum(1 for label, _href in toc_links if label == INTRO_LABEL)
    summary_count = sum(1 for label, _href in toc_links if label == "Summary and Central Argument")

    return {
        "parse_errors": parse_errors,
        "duplicate_ids": duplicate_ids,
        "broken_links": sorted(set(broken_links)),
        "old_file_absent": not (workdir / OLD_FILE).exists(),
        "new_file_present": (workdir / NEW_FILE).exists(),
        "summary_label_count_nav": summary_count,
        "introduction_label_count_nav": intro_count,
        "summary_label_count_ncx": ncx_labels.count("Summary and Central Argument"),
        "introduction_label_count_ncx": ncx_labels.count(INTRO_LABEL),
        "nav_linear": nav_ref.get("linear") if nav_ref is not None else None,
        "spine_hrefs": spine_hrefs,
        "epilogue_before_glossary": (
            "text/epilogue.xhtml" in spine_hrefs
            and "text/glossary.xhtml" in spine_hrefs
            and spine_hrefs.index("text/epilogue.xhtml") < spine_hrefs.index("text/glossary.xhtml")
        ),
        "introduction_before_book_1": (
            "text/introduction.xhtml" in spine_hrefs
            and "text/book_01.xhtml" in spine_hrefs
            and spine_hrefs.index("text/introduction.xhtml") < spine_hrefs.index("text/book_01.xhtml")
        ),
        "toc_links": toc_links,
    }


def scan_artifacts(workdir: Path) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {term: [] for term in SCAN_TERMS}
    for path in sorted((workdir / "EPUB").rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".xhtml", ".opf", ".ncx", ".css"}:
            continue
        rel = path.relative_to(workdir).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for term in SCAN_TERMS:
            if term in text:
                hits[term].append(rel)
    return {term: files for term, files in hits.items() if files}


def main() -> None:
    if not INPUT_EPUB.exists():
        raise FileNotFoundError(INPUT_EPUB)

    with tempfile.TemporaryDirectory(prefix="republic_intro_v05_") as tmp:
        workdir = Path(tmp)
        extract_epub(INPUT_EPUB, workdir)
        renamed = rename_intro_file(workdir)
        opf_result = update_opf(workdir)
        nav_updated = update_nav(workdir)
        ncx_updated = update_ncx(workdir)
        validation = validate(workdir)
        scan_hits = scan_artifacts(workdir)

        blocked = bool(
            validation["parse_errors"]
            or validation["duplicate_ids"]
            or validation["broken_links"]
            or scan_hits
            or not validation["old_file_absent"]
            or not validation["new_file_present"]
            or validation["summary_label_count_nav"] != 0
            or validation["summary_label_count_ncx"] != 0
            or validation["introduction_label_count_nav"] != 1
            or validation["introduction_label_count_ncx"] != 1
            or validation["nav_linear"] != "no"
            or not validation["epilogue_before_glossary"]
            or not validation["introduction_before_book_1"]
        )
        final_status = "BLOCKED_NEEDS_FIX" if blocked else "READY_FOR_KINDLE_PREVIEWER"
        write_epub(workdir, OUTPUT_EPUB)

    report = {
        "input_epub": str(INPUT_EPUB.relative_to(ROOT)),
        "input_sha256": sha256(INPUT_EPUB),
        "output_epub": str(OUTPUT_EPUB.relative_to(ROOT)),
        "output_sha256": sha256(OUTPUT_EPUB),
        "renamed_file": renamed,
        "old_file": OLD_FILE,
        "new_file": NEW_FILE,
        "introduction_subtitle": INTRO_SUBTITLE,
        "opf_updated": bool(opf_result),
        "nav_updated": nav_updated,
        "ncx_updated": ncx_updated,
        "artifact_scan": scan_hits,
        "validation": validation,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "final_status": final_status,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("REPUBLIC V05 FINAL EXPORT COMPLETE")
    print()
    print("Generated:")
    print(str(OUTPUT_EPUB.relative_to(ROOT)))
    print(str(REPORT_PATH.relative_to(ROOT)))
    print()
    print("Status:")
    print(final_status)
    if final_status != "READY_FOR_KINDLE_PREVIEWER":
        print(json.dumps({"artifact_scan": scan_hits, "validation": validation}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
