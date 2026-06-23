#!/usr/bin/env python3
"""Finalize The Republic v08 with structure-preserving EPUB QA only."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import tempfile
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urldefrag

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[2]
INPUT_EPUB = ROOT / "data/builds/book_0027/en/republic_of_plato_v07_READY.epub"
OUTPUT_EPUB = ROOT / "data/builds/book_0027/en/republic_of_plato_v08_FINAL.epub"
REPORT_PATH = ROOT / "data/builds/book_0027/en/republic_v08_final_report.json"

READING_ORDER = [
    "Cover",
    "Title Page",
    "Frontispiece",
    "Copyright",
    "About This Book",
    "Introduction",
    "Book 1",
    "Book 2",
    "Book 3",
    "Book 4",
    "Book 5",
    "Book 6",
    "Book 7",
    "Book 8",
    "Book 9",
    "Book 10",
    "Epilogue",
    "Glossary of Key Terms and Names",
]

READING_ORDER_HREFS = [
    "text/cover.xhtml",
    "text/title_page.xhtml",
    "text/frontispiece.xhtml",
    "text/copyright.xhtml",
    "text/about_this_book.xhtml",
    "text/introduction.xhtml",
    *[f"text/book_{i:02d}.xhtml" for i in range(1, 11)],
    "text/epilogue.xhtml",
    "text/glossary.xhtml",
]

PRESERVE_UNCHANGED = [
    "EPUB/text/about_this_book.xhtml",
    "EPUB/text/introduction.xhtml",
    "EPUB/text/epilogue.xhtml",
    "EPUB/text/book_02.xhtml",
    "EPUB/text/book_03.xhtml",
    "EPUB/text/book_04.xhtml",
    "EPUB/text/book_05.xhtml",
    "EPUB/text/book_06.xhtml",
    "EPUB/text/book_07.xhtml",
    "EPUB/text/book_08.xhtml",
    "EPUB/text/book_09.xhtml",
    "EPUB/text/book_10.xhtml",
]

TITLE_PAGE_EXPECTED_LINES = [
    "The Republic",
    "Modern English Edition",
    "by Plato",
    "Adapted by Péricles Guará Silva",
    "RinoBooks",
    "Rio de Janeiro, Brazil · 2026",
]

REFS_TO_REMOVE = {
    "ref-01-adeimantus": "Adeimantus",
    "ref-05-cephalus": "Cephalus",
    "ref-12-glaucon": "Glaucon",
    "ref-22-piraeus": "Piraeus",
    "ref-23-plato": "Plato",
    "ref-24-polemarchus": "Polemarchus",
    "ref-27-socrates": "Socrates",
    "ref-32-thrasymachus": "Thrasymachus",
}

GLOSSARY_IDS_TO_REMOVE_BACKLINKS = {
    "glossary-01-adeimantus": "Adeimantus",
    "glossary-05-cephalus": "Cephalus",
    "glossary-12-glaucon": "Glaucon",
    "glossary-22-piraeus": "Piraeus",
    "glossary-23-plato": "Plato",
    "glossary-24-polemarchus": "Polemarchus",
    "glossary-27-socrates": "Socrates",
    "glossary-32-thrasymachus": "Thrasymachus",
}

SCAN_TERMS = [
    "Adapted Preface",
    "Modern en Edition",
    "Rinobooks",
    "Plato of Athens",
    "Book 02 - begins",
    "Summary and Central Argument",
    ":::",
    "Markdown Preview",
    "Back to Editions",
    "Edit this Edition",
    "/home/",
    "data/builds/",
    "\\...",
    ";---",
    "Book: book_",
    "Language:",
    "File:",
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
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


def read_soup(path: Path) -> BeautifulSoup:
    return BeautifulSoup(path.read_text(encoding="utf-8"), "xml")


def write_soup(path: Path, soup: BeautifulSoup) -> None:
    path.write_text(str(soup), encoding="utf-8")


def update_opf(workdir: Path) -> dict[str, object]:
    path = workdir / "EPUB/content.opf"
    soup = read_soup(path)
    manifest = soup.find("manifest")
    spine = soup.find("spine")
    metadata = soup.find("metadata")
    if manifest is None or spine is None:
        raise ValueError("OPF manifest or spine not found")

    nav_item = soup.find("item", id="nav")
    if nav_item is None:
        raise ValueError("OPF nav manifest item not found")
    nav_item["properties"] = "nav"

    nav_ref = soup.find("itemref", idref="nav")
    if nav_ref is None:
        nav_ref = soup.new_tag("itemref")
        nav_ref["idref"] = "nav"
        title_ref = soup.find("itemref", idref="text_title_page_xhtml")
        if title_ref is not None:
            title_ref.insert_after(nav_ref)
        else:
            spine.insert(0, nav_ref)
    nav_ref["linear"] = "no"

    if metadata is not None:
        modified = metadata.find("meta", attrs={"property": "dcterms:modified"})
        if modified is None:
            modified = soup.new_tag("meta")
            modified["property"] = "dcterms:modified"
            metadata.append(modified)
        modified.string = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    write_soup(path, soup)
    return spine_state(workdir)


def remove_character_glossary_refs(workdir: Path) -> tuple[list[str], Counter[str]]:
    removed: list[str] = []
    retained_counter: Counter[str] = Counter()
    for path in sorted((workdir / "EPUB/text").glob("book_*.xhtml")):
        soup = read_soup(path)
        changed = False
        for anchor in list(soup.find_all("a", id=True, href=True)):
            ref_id = str(anchor.get("id"))
            if ref_id in REFS_TO_REMOVE:
                label = REFS_TO_REMOVE[ref_id]
                parent = anchor.parent
                if parent is not None and parent.name == "sup":
                    parent.decompose()
                else:
                    anchor.decompose()
                removed.append(label)
                changed = True
                continue
            if str(anchor.get("id", "")).startswith("ref-"):
                retained_counter[str(anchor.get("href", ""))] += 1
        if changed:
            write_soup(path, soup)
    return removed, retained_counter


def update_glossary_backlinks(workdir: Path) -> list[str]:
    path = workdir / "EPUB/text/glossary.xhtml"
    soup = read_soup(path)
    updated: list[str] = []
    for glossary_id, label in GLOSSARY_IDS_TO_REMOVE_BACKLINKS.items():
        section = soup.find(id=glossary_id)
        if section is None:
            continue
        backlink = section.find("p", class_="glossary-backlink")
        if backlink is None:
            backlink = soup.new_tag("p")
            backlink["class"] = "glossary-backlink"
            section.append(backlink)
        backlink.clear()
        backlink.string = "No direct body reference inserted in this edition."
        updated.append(label)
    write_soup(path, soup)
    return updated


def collect_ids(workdir: Path) -> tuple[dict[str, set[str]], list[str]]:
    ids_by_file: dict[str, set[str]] = {}
    duplicates: list[str] = []
    for path in sorted((workdir / "EPUB").rglob("*")):
        if path.suffix.lower() not in {".xhtml", ".html", ".opf", ".ncx"}:
            continue
        rel = path.relative_to(workdir).as_posix()
        soup = read_soup(path)
        ids: set[str] = set()
        for node in soup.find_all(attrs={"id": True}):
            node_id = str(node["id"])
            if node_id in ids:
                duplicates.append(f"{rel}#{node_id}")
            ids.add(node_id)
        ids_by_file[rel] = ids
    return ids_by_file, sorted(set(duplicates))


def resolve_href(source_rel: str, href: str) -> tuple[str, str]:
    target, fragment = urldefrag(href)
    base_dir = posixpath.dirname(source_rel)
    target_rel = posixpath.normpath(posixpath.join(base_dir, unquote(target))) if target else source_rel
    return target_rel, fragment


def validate_links(workdir: Path) -> dict[str, object]:
    ids_by_file, duplicates = collect_ids(workdir)
    existing = {path.relative_to(workdir).as_posix() for path in workdir.rglob("*") if path.is_file()}
    broken: list[str] = []

    def check_ref(source_rel: str, href: str) -> None:
        if not href or re.match(r"^[a-z][a-z0-9+.-]*:", href):
            return
        target_rel, fragment = resolve_href(source_rel, href)
        if target_rel not in existing:
            broken.append(f"{source_rel}: missing target {href}")
        elif fragment and fragment not in ids_by_file.get(target_rel, set()):
            broken.append(f"{source_rel}: missing fragment {href}")

    for path in sorted((workdir / "EPUB").rglob("*")):
        if path.suffix.lower() not in {".xhtml", ".html", ".ncx", ".opf"}:
            continue
        rel = path.relative_to(workdir).as_posix()
        soup = read_soup(path)
        for node in soup.find_all(href=True):
            check_ref(rel, str(node["href"]))
        for node in soup.find_all(src=True):
            check_ref(rel, str(node["src"]))
        for node in soup.find_all("content", src=True):
            check_ref(rel, str(node["src"]))
    return {"duplicate_ids": duplicates, "broken_links": sorted(set(broken))}


def scan_artifacts(workdir: Path) -> dict[str, list[str]]:
    hits = {term: [] for term in SCAN_TERMS}
    for path in sorted((workdir / "EPUB").rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".xhtml", ".opf", ".ncx", ".css", ".xml"}:
            continue
        rel = path.relative_to(workdir).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for term in SCAN_TERMS:
            if term in text:
                hits[term].append(rel)
    return {term: files for term, files in hits.items() if files}


def spine_state(workdir: Path) -> dict[str, object]:
    opf = read_soup(workdir / "EPUB/content.opf")
    manifest = {item.get("id"): item.get("href") for item in opf.find_all("item")}
    spine = [
        {
            "idref": ref.get("idref"),
            "href": manifest.get(ref.get("idref")),
            "linear": ref.get("linear"),
        }
        for ref in opf.find_all("itemref")
    ]
    linear_hrefs = [item["href"] for item in spine if item["linear"] != "no"]
    hrefs = [item["href"] for item in spine]
    return {
        "spine": spine,
        "linear_reading_hrefs": linear_hrefs,
        "nav_in_manifest": opf.find("item", id="nav", properties=re.compile(r"\bnav\b")) is not None,
        "nav_non_linear": any(item["href"] == "nav.xhtml" and item["linear"] == "no" for item in spine),
        "reading_order_matches_expected": linear_hrefs == READING_ORDER_HREFS,
        "epilogue_before_glossary": (
            "text/epilogue.xhtml" in hrefs
            and "text/glossary.xhtml" in hrefs
            and hrefs.index("text/epilogue.xhtml") < hrefs.index("text/glossary.xhtml")
        ),
    }


def manifest_check(workdir: Path) -> dict[str, object]:
    opf = read_soup(workdir / "EPUB/content.opf")
    names = {path.relative_to(workdir / "EPUB").as_posix() for path in (workdir / "EPUB").rglob("*") if path.is_file()}
    manifest_items = {item.get("id"): item.get("href") for item in opf.find_all("item")}
    missing_manifest_files = sorted(
        href for href in manifest_items.values() if href and href not in names
    )
    missing_spine_idrefs = sorted(
        ref.get("idref")
        for ref in opf.find_all("itemref")
        if ref.get("idref") not in manifest_items
    )
    return {
        "manifest_missing_files": missing_manifest_files,
        "spine_idrefs_missing_manifest": missing_spine_idrefs,
    }


def navigation_labels(workdir: Path) -> dict[str, object]:
    nav = read_soup(workdir / "EPUB/nav.xhtml")
    toc_nav = nav.find("nav", attrs={"epub:type": "toc"}) or nav.find("nav", id="toc")
    nav_labels = [
        node.get_text(" ", strip=True)
        for node in (toc_nav.find_all("a") if toc_nav else [])
    ]
    ncx_path = workdir / "EPUB/toc.ncx"
    ncx_labels: list[str] = []
    if ncx_path.exists():
        ncx = read_soup(ncx_path)
        for point in ncx.find_all("navPoint"):
            text = point.find("text")
            if text is not None:
                ncx_labels.append(text.get_text(" ", strip=True))
    return {
        "nav_labels": nav_labels,
        "ncx_labels": ncx_labels,
        "nav_ncx_match": not ncx_labels or nav_labels == ncx_labels,
    }


def container_check(workdir: Path) -> dict[str, object]:
    container = read_soup(workdir / "META-INF/container.xml")
    rootfile = container.find("rootfile")
    return {
        "container_points_to_opf": rootfile is not None and rootfile.get("full-path") == "EPUB/content.opf"
    }


def mimetype_check(epub_path: Path) -> dict[str, object]:
    with zipfile.ZipFile(epub_path) as zf:
        first = zf.infolist()[0]
        return {
            "mimetype_first": first.filename == "mimetype",
            "mimetype_uncompressed": first.compress_type == zipfile.ZIP_STORED,
            "zip_test": zf.testzip(),
        }


def title_and_metadata_check(workdir: Path) -> dict[str, object]:
    title_page = read_soup(workdir / "EPUB/text/title_page.xhtml")
    title_page_text = [
        node.get_text(" ", strip=True)
        for node in title_page.find(id="title-page").find_all(["h1", "p"])
    ]
    opf = read_soup(workdir / "EPUB/content.opf")
    contributor = opf.find("contributor")
    contributor_id = contributor.get("id") if contributor else None
    role = opf.find("meta", attrs={"refines": f"#{contributor_id}", "property": "role"}) if contributor_id else None
    return {
        "title_page_matches": title_page_text == TITLE_PAGE_EXPECTED_LINES,
        "title_page_text": title_page_text,
        "dc_title": opf.find("title").get_text(strip=True) if opf.find("title") else None,
        "dc_creator": opf.find("creator").get_text(strip=True) if opf.find("creator") else None,
        "dc_publisher": opf.find("publisher").get_text(strip=True) if opf.find("publisher") else None,
        "dc_language": opf.find("language").get_text(strip=True) if opf.find("language") else None,
        "contributor": contributor.get_text(strip=True) if contributor else None,
        "contributor_role": role.get_text(strip=True) if role else None,
    }


def content_structure_check(workdir: Path) -> dict[str, object]:
    issues: list[str] = []
    book_starts = {
        "EPUB/text/book_01.xhtml": "I went down yesterday to the Piraeus with Glaucon",
        "EPUB/text/book_02.xhtml": "With these words I thought I had brought the discussion to an end",
    }
    for rel, expected in book_starts.items():
        text = read_soup(workdir / rel).get_text(" ", strip=True)
        if expected not in text:
            issues.append(f"{rel} does not contain expected opening: {expected}")
    for i in range(1, 11):
        path = workdir / f"EPUB/text/book_{i:02d}.xhtml"
        if not path.exists():
            issues.append(f"Book {i} file missing")
    duplicate_checks = {
        "Copyright": "EPUB/text/copyright.xhtml",
        "About This Book": "EPUB/text/about_this_book.xhtml",
        "Introduction": "EPUB/text/introduction.xhtml",
    }
    for label, rel in duplicate_checks.items():
        soup = read_soup(workdir / rel)
        matching_h1 = [
            heading.get_text(" ", strip=True)
            for heading in soup.find_all("h1")
            if heading.get_text(" ", strip=True) == label
        ]
        if len(matching_h1) != 1:
            issues.append(f"Expected exactly one h1 '{label}' in {rel}; found {len(matching_h1)}")
    return {"issues": issues}


def glossary_state(workdir: Path, removed: list[str], backlinks_updated: list[str]) -> dict[str, object]:
    soup = read_soup(workdir / "EPUB/text/glossary.xhtml")
    entries = [node for node in soup.find_all("section") if str(node.get("id", "")).startswith("glossary-")]
    requested_labels = list(REFS_TO_REMOVE.values())
    actual_removed = sorted(set(removed), key=requested_labels.index)
    already_absent = [label for label in requested_labels if label not in actual_removed]
    retained_refs: list[str] = []
    for path in sorted((workdir / "EPUB/text").glob("book_*.xhtml")):
        book = read_soup(path)
        for anchor in book.find_all("a", id=True, href=True):
            ref_id = str(anchor.get("id"))
            if ref_id.startswith("ref-"):
                retained_refs.append(ref_id.removeprefix("ref-"))
    return {
        "entries_retained": len(entries),
        "body_references_removed": requested_labels,
        "body_references_removed_actual": actual_removed,
        "body_references_already_absent": already_absent,
        "backlinks_updated": sorted(backlinks_updated, key=lambda label: list(REFS_TO_REMOVE.values()).index(label)),
        "body_references_retained_for_concepts": retained_refs,
    }


def main() -> None:
    if not INPUT_EPUB.exists():
        raise FileNotFoundError(INPUT_EPUB)

    with zipfile.ZipFile(INPUT_EPUB) as zin:
        before_hashes = {name: sha256_bytes(zin.read(name)) for name in PRESERVE_UNCHANGED}

    with tempfile.TemporaryDirectory(prefix="republic_v08_final_") as tmp:
        workdir = Path(tmp)
        extract_epub(INPUT_EPUB, workdir)

        opf_state = update_opf(workdir)
        removed_refs, _ = remove_character_glossary_refs(workdir)
        backlinks_updated = update_glossary_backlinks(workdir)
        link_validation = validate_links(workdir)
        artifact_hits = scan_artifacts(workdir)
        manifest = manifest_check(workdir)
        navigation = navigation_labels(workdir)
        container = container_check(workdir)
        metadata = title_and_metadata_check(workdir)
        content = content_structure_check(workdir)
        glossary = glossary_state(workdir, removed_refs, backlinks_updated)
        after_hashes = {name: sha256_bytes((workdir / name).read_bytes()) for name in PRESERVE_UNCHANGED}
        preserved_unchanged = {
            name: before_hashes[name] == after_hashes[name]
            for name in PRESERVE_UNCHANGED
        }

        pre_write_issues = []
        if not opf_state["nav_non_linear"]:
            pre_write_issues.append("nav.xhtml is not non-linear in OPF spine")
        if not opf_state["nav_in_manifest"]:
            pre_write_issues.append("nav.xhtml manifest item missing properties='nav'")
        if not opf_state["reading_order_matches_expected"]:
            pre_write_issues.append("linear reading order does not match expected structure")
        if not opf_state["epilogue_before_glossary"]:
            pre_write_issues.append("Epilogue does not appear before glossary")
        if link_validation["broken_links"] or link_validation["duplicate_ids"]:
            pre_write_issues.append("broken links or duplicate IDs found")
        if artifact_hits:
            pre_write_issues.append("forbidden artifact scan found EPUB content hits")
        if manifest["manifest_missing_files"] or manifest["spine_idrefs_missing_manifest"]:
            pre_write_issues.append("manifest or spine reference issue found")
        if not navigation["nav_ncx_match"]:
            pre_write_issues.append("nav.xhtml and toc.ncx labels do not match")
        if not container["container_points_to_opf"]:
            pre_write_issues.append("container.xml does not point to EPUB/content.opf")
        if not metadata["title_page_matches"]:
            pre_write_issues.append("title page visible content does not match expected text")
        if metadata["dc_title"] != "The Republic: Modern English Edition":
            pre_write_issues.append("dc:title mismatch")
        if metadata["dc_creator"] != "Plato":
            pre_write_issues.append("dc:creator mismatch")
        if metadata["dc_publisher"] != "RinoBooks":
            pre_write_issues.append("dc:publisher mismatch")
        if metadata["dc_language"] != "en":
            pre_write_issues.append("dc:language mismatch")
        if metadata["contributor"] != "Péricles Guará Silva" or metadata["contributor_role"] != "adp":
            pre_write_issues.append("contributor adapter metadata mismatch")
        if content["issues"]:
            pre_write_issues.extend(content["issues"])
        if not all(preserved_unchanged.values()):
            pre_write_issues.append("protected non-target content changed")
        if glossary["entries_retained"] != 36:
            pre_write_issues.append("glossary entry count is not 36")

        write_epub(workdir, OUTPUT_EPUB)

    mime = mimetype_check(OUTPUT_EPUB)
    post_write_issues = list(pre_write_issues)
    if mime["zip_test"] is not None:
        post_write_issues.append(f"EPUB zip test failed at {mime['zip_test']}")
    if not mime["mimetype_first"] or not mime["mimetype_uncompressed"]:
        post_write_issues.append("mimetype is not first and uncompressed")

    final_status = "READY_FOR_KINDLE_PREVIEWER" if not post_write_issues else "BLOCKED_NEEDS_FIX"

    report = {
        "input_epub": str(INPUT_EPUB.relative_to(ROOT)),
        "input_sha256": sha256_file(INPUT_EPUB),
        "output_epub": str(OUTPUT_EPUB.relative_to(ROOT)),
        "output_sha256": sha256_file(OUTPUT_EPUB),
        "structure_preserved": not post_write_issues,
        "fixes_applied": {
            "nav_set_non_linear": opf_state["nav_non_linear"],
            "excessive_character_glossary_refs_removed": sorted(set(removed_refs), key=lambda label: list(REFS_TO_REMOVE.values()).index(label)),
            "glossary_backlinks_updated": sorted(set(backlinks_updated), key=lambda label: list(REFS_TO_REMOVE.values()).index(label)),
            "opf_modified_timestamp_updated": True,
        },
        "glossary": {
            **glossary,
            "broken_links": link_validation["broken_links"],
            "duplicate_ids": link_validation["duplicate_ids"],
        },
        "artifact_scan": {
            "status": "PASS" if not artifact_hits else "FAIL",
            "issues": artifact_hits,
        },
        "structure_check": {
            "status": "PASS" if not post_write_issues else "FAIL",
            "reading_order": READING_ORDER,
            "linear_reading_hrefs": opf_state["linear_reading_hrefs"],
            "spine": opf_state["spine"],
            "manifest": manifest,
            "navigation": navigation,
            "container": container,
            "mimetype": mime,
            "metadata": metadata,
            "content_issues": content["issues"],
            "protected_files_unchanged": preserved_unchanged,
            "blocking_issues": post_write_issues,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "final_status": final_status,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("THE REPUBLIC v08 FINAL QA COMPLETE")
    print()
    print("Generated:")
    print(str(OUTPUT_EPUB.relative_to(ROOT)))
    print(str(REPORT_PATH.relative_to(ROOT)))
    print()
    print("Status:")
    print(final_status)
    if post_write_issues:
        print()
        print("Blocking issues:")
        for issue in post_write_issues:
            print(f"- {issue}")


if __name__ == "__main__":
    main()
