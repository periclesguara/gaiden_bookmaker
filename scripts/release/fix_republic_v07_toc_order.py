#!/usr/bin/env python3
"""Create Republic v07 with the table of contents in the reading flow."""

from __future__ import annotations

import hashlib
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
INPUT_EPUB = ROOT / "data/builds/book_0027/en/republic_of_plato_v06_READY.epub"
OUTPUT_EPUB = ROOT / "data/builds/book_0027/en/republic_of_plato_v07_READY.epub"
REPORT_PATH = ROOT / "data/builds/book_0027/en/republic_of_plato_v07_READY_report.json"
DOWNLOAD_DIR = Path("/home/periclesguara/Downloads/Republic of Plato")
DOWNLOAD_EPUB = DOWNLOAD_DIR / "republic_of_plato_v07_READY.epub"
DOWNLOAD_REPORT = DOWNLOAD_DIR / "republic_of_plato_v07_READY_report.json"

PRESERVE_FILES = [
    "EPUB/text/title_page.xhtml",
    "EPUB/text/frontispiece.xhtml",
    "EPUB/text/copyright.xhtml",
    "EPUB/text/about_this_book.xhtml",
    "EPUB/text/introduction.xhtml",
    "EPUB/text/epilogue.xhtml",
    "EPUB/text/glossary.xhtml",
    *[f"EPUB/text/book_{i:02d}.xhtml" for i in range(1, 11)],
]

SCAN_TERMS = [
    "Plato of Athens",
    "Rinobooks",
    "Modern en Edition",
    "Adapted Preface",
    "Book 02 - begins",
    ":::",
    "Markdown Preview",
    "Back to Editions",
    "/home/",
    "data/builds/",
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


def update_opf_toc_order(workdir: Path) -> dict[str, object]:
    path = workdir / "EPUB/content.opf"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
    spine = soup.find("spine")
    if spine is None:
        raise ValueError("OPF spine not found")

    nav_ref = soup.find("itemref", idref="nav")
    title_ref = soup.find("itemref", idref="text_title_page_xhtml")
    if nav_ref is None:
        nav_ref = soup.new_tag("itemref")
        nav_ref["idref"] = "nav"
    else:
        nav_ref.extract()

    # Make TOC part of the linear reading order and place it after Title Page.
    nav_ref.attrs.pop("linear", None)
    if title_ref is not None:
        title_ref.insert_after(nav_ref)
    else:
        spine.insert(0, nav_ref)

    metadata = soup.find("metadata")
    if metadata is not None:
        modified = metadata.find("meta", attrs={"property": "dcterms:modified"})
        if modified is None:
            modified = soup.new_tag("meta")
            modified["property"] = "dcterms:modified"
            metadata.append(modified)
        modified.string = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    path.write_text(str(soup), encoding="utf-8")
    return spine_state(workdir)


def update_nav_heading(workdir: Path) -> bool:
    path = workdir / "EPUB/nav.xhtml"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
    heading = soup.find("nav", id="toc").find("h1") if soup.find("nav", id="toc") else soup.find("h1")
    if heading is None:
        return False
    heading["id"] = "toc-title"
    heading.string = "Table of Contents"
    path.write_text(str(soup), encoding="utf-8")
    return True


def spine_state(workdir: Path) -> dict[str, object]:
    opf = BeautifulSoup((workdir / "EPUB/content.opf").read_text(encoding="utf-8"), "xml")
    manifest = {item.get("id"): item.get("href") for item in opf.find_all("item")}
    spine = [
        {
            "idref": ref.get("idref"),
            "href": manifest.get(ref.get("idref")),
            "linear": ref.get("linear"),
        }
        for ref in opf.find_all("itemref")
    ]
    hrefs = [item["href"] for item in spine]
    return {
        "spine": spine,
        "toc_after_title_page": (
            "nav.xhtml" in hrefs
            and "text/title_page.xhtml" in hrefs
            and hrefs.index("nav.xhtml") == hrefs.index("text/title_page.xhtml") + 1
        ),
        "toc_linear": next((item["linear"] for item in spine if item["href"] == "nav.xhtml"), None) is None,
        "epilogue_before_glossary": (
            "text/epilogue.xhtml" in hrefs
            and "text/glossary.xhtml" in hrefs
            and hrefs.index("text/epilogue.xhtml") < hrefs.index("text/glossary.xhtml")
        ),
    }


def collect_ids(workdir: Path) -> tuple[dict[str, set[str]], list[str]]:
    ids_by_file: dict[str, set[str]] = {}
    duplicates: list[str] = []
    for path in sorted((workdir / "EPUB").rglob("*.xhtml")):
        rel = path.relative_to(workdir).as_posix()
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
        ids: set[str] = set()
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


def validate_links(workdir: Path) -> dict[str, object]:
    ids_by_file, duplicates = collect_ids(workdir)
    existing = {path.relative_to(workdir).as_posix() for path in workdir.rglob("*") if path.is_file()}
    broken: list[str] = []
    for path in sorted((workdir / "EPUB").rglob("*.xhtml")):
        rel = path.relative_to(workdir).as_posix()
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
        for anchor in soup.find_all("a", href=True):
            href = str(anchor["href"])
            if re.match(r"^[a-z]+:", href):
                continue
            target_rel, fragment = resolve_href(rel, href)
            if target_rel not in existing:
                broken.append(f"{rel}: missing target {href}")
            elif fragment and fragment not in ids_by_file.get(target_rel, set()):
                broken.append(f"{rel}: missing fragment {href}")
    return {"duplicate_ids": duplicates, "broken_links": sorted(set(broken))}


def scan_artifacts(workdir: Path) -> dict[str, list[str]]:
    hits = {term: [] for term in SCAN_TERMS}
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

    with zipfile.ZipFile(INPUT_EPUB) as zin:
        before_hashes = {name: sha256_bytes(zin.read(name)) for name in PRESERVE_FILES}

    with tempfile.TemporaryDirectory(prefix="republic_v07_toc_") as tmp:
        workdir = Path(tmp)
        extract_epub(INPUT_EPUB, workdir)
        opf_spine = update_opf_toc_order(workdir)
        nav_heading_updated = update_nav_heading(workdir)
        link_validation = validate_links(workdir)
        artifact_scan = scan_artifacts(workdir)
        after_hashes = {name: sha256_bytes((workdir / name).read_bytes()) for name in PRESERVE_FILES}
        preserved_unchanged = {
            name: before_hashes[name] == after_hashes[name]
            for name in PRESERVE_FILES
        }
        final_status = "READY_FOR_KINDLE_PREVIEWER"
        if (
            not opf_spine["toc_after_title_page"]
            or not opf_spine["toc_linear"]
            or not opf_spine["epilogue_before_glossary"]
            or not nav_heading_updated
            or artifact_scan
            or link_validation["broken_links"]
            or link_validation["duplicate_ids"]
            or not all(preserved_unchanged.values())
        ):
            final_status = "BLOCKED_NEEDS_FIX"
        write_epub(workdir, OUTPUT_EPUB)

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OUTPUT_EPUB, DOWNLOAD_EPUB)

    report = {
        "input_epub": str(INPUT_EPUB.relative_to(ROOT)),
        "input_sha256": sha256_file(INPUT_EPUB),
        "output_epub": str(OUTPUT_EPUB.relative_to(ROOT)),
        "output_sha256": sha256_file(OUTPUT_EPUB),
        "download_epub": str(DOWNLOAD_EPUB),
        "download_sha256": sha256_file(DOWNLOAD_EPUB),
        "toc_fix": "nav.xhtml is now linear and placed immediately after Title Page in the spine.",
        "nav_heading_updated_to": "Table of Contents",
        "spine_validation": opf_spine,
        "preserved_files_unchanged": preserved_unchanged,
        "artifact_scan": artifact_scan,
        "link_validation": link_validation,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "final_status": final_status,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(REPORT_PATH, DOWNLOAD_REPORT)

    print("REPUBLIC V07 READY EXPORT COMPLETE")
    print()
    print("Generated:")
    print(str(OUTPUT_EPUB.relative_to(ROOT)))
    print(str(DOWNLOAD_EPUB))
    print(str(REPORT_PATH.relative_to(ROOT)))
    print()
    print("Status:")
    print(final_status)
    if final_status != "READY_FOR_KINDLE_PREVIEWER":
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
