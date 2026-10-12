#!/usr/bin/env python3
"""Apply final metadata/title-page micro-fix to The Republic EPUB."""

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
INPUT_EPUB = ROOT / "data/builds/book_0027/en/republic_of_plato_v05_FINAL.epub"
OUTPUT_EPUB = ROOT / "data/builds/book_0027/en/republic_of_plato_v06_READY.epub"
REPORT_PATH = ROOT / "data/builds/book_0027/en/republic_of_plato_v06_READY_report.json"

PRESERVE_FILES = [
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


def update_title_page(workdir: Path) -> None:
    path = workdir / "EPUB/text/title_page.xhtml"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
    section = soup.find("section", id="title-page")
    if section is None:
        body = soup.find("body")
        if body is None:
            raise ValueError("title_page.xhtml body not found")
        section = soup.new_tag("section")
        section["id"] = "title-page"
        section["epub:type"] = "titlepage"
        body.clear()
        body.append(section)
    section.clear()
    section["id"] = "title-page"
    section["epub:type"] = "titlepage"

    h1 = soup.new_tag("h1")
    h1.string = "The Republic"
    section.append(h1)
    for value in (
        "Modern English Edition",
        "by Plato",
        "Adapted by Péricles Guará Silva",
        "RinoBooks",
        "Rio de Janeiro, Brazil · 2026",
    ):
        p = soup.new_tag("p")
        p.string = value
        section.append(p)
    path.write_text(str(soup), encoding="utf-8")


def update_opf(workdir: Path) -> str:
    path = workdir / "EPUB/content.opf"
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
    metadata = soup.find("metadata")
    if metadata is None:
        raise ValueError("OPF metadata not found")

    def set_text(tag_name: str, value: str) -> None:
        node = metadata.find(tag_name)
        if node is None:
            node = soup.new_tag(tag_name)
            metadata.append(node)
        node.string = value

    set_text("dc:title", "The Republic: Modern English Edition")
    set_text("dc:creator", "Plato")
    set_text("dc:publisher", "RinoBooks")
    set_text("dc:language", "en")

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

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    modified = metadata.find("meta", attrs={"property": "dcterms:modified"})
    if modified is None:
        modified = soup.new_tag("meta")
        modified["property"] = "dcterms:modified"
        metadata.append(modified)
    modified.string = timestamp

    path.write_text(str(soup), encoding="utf-8")
    return timestamp


def collect_ids(workdir: Path) -> tuple[dict[str, set[str]], list[str]]:
    ids_by_file: dict[str, set[str]] = {}
    duplicate_ids: list[str] = []
    for path in sorted((workdir / "EPUB").rglob("*.xhtml")):
        rel = path.relative_to(workdir).as_posix()
        ids: set[str] = set()
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
        for node in soup.find_all(attrs={"id": True}):
            node_id = str(node["id"])
            if node_id in ids:
                duplicate_ids.append(f"{rel}#{node_id}")
            ids.add(node_id)
        ids_by_file[rel] = ids
    return ids_by_file, sorted(set(duplicate_ids))


def resolve_href(source_rel: str, href: str) -> tuple[str, str]:
    target, _, fragment = href.partition("#")
    base = posixpath.dirname(source_rel)
    target_rel = posixpath.normpath(posixpath.join(base, target)) if target else source_rel
    return target_rel, fragment


def validate_links(workdir: Path) -> dict[str, object]:
    ids_by_file, duplicate_ids = collect_ids(workdir)
    existing = {path.relative_to(workdir).as_posix() for path in workdir.rglob("*") if path.is_file()}
    broken_links: list[str] = []
    for path in sorted((workdir / "EPUB").rglob("*.xhtml")):
        rel = path.relative_to(workdir).as_posix()
        soup = BeautifulSoup(path.read_text(encoding="utf-8"), "xml")
        for anchor in soup.find_all("a", href=True):
            href = str(anchor["href"])
            if re.match(r"^[a-z]+:", href):
                continue
            target_rel, fragment = resolve_href(rel, href)
            if target_rel not in existing:
                broken_links.append(f"{rel}: missing target {href}")
            elif fragment and fragment not in ids_by_file.get(target_rel, set()):
                broken_links.append(f"{rel}: missing fragment {href}")
    return {"duplicate_ids": duplicate_ids, "broken_links": sorted(set(broken_links))}


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


def opf_metadata(workdir: Path) -> dict[str, str]:
    soup = BeautifulSoup((workdir / "EPUB/content.opf").read_text(encoding="utf-8"), "xml")
    metadata = {}
    for key in ("dc:title", "dc:creator", "dc:publisher", "dc:language", "dc:contributor"):
        node = soup.find(key)
        metadata[key] = node.get_text(strip=True) if node else ""
    role = soup.find("meta", attrs={"refines": "#epub-contributor-1", "property": "role"})
    metadata["contributor_role"] = role.get_text(strip=True) if role else ""
    modified = soup.find("meta", attrs={"property": "dcterms:modified"})
    metadata["dcterms:modified"] = modified.get_text(strip=True) if modified else ""
    return metadata


def main() -> None:
    if not INPUT_EPUB.exists():
        raise FileNotFoundError(INPUT_EPUB)

    with zipfile.ZipFile(INPUT_EPUB) as zin:
        before_hashes = {name: sha256_bytes(zin.read(name)) for name in PRESERVE_FILES}

    with tempfile.TemporaryDirectory(prefix="republic_v06_") as tmp:
        workdir = Path(tmp)
        extract_epub(INPUT_EPUB, workdir)
        update_title_page(workdir)
        modified_timestamp = update_opf(workdir)
        link_validation = validate_links(workdir)
        artifact_scan = scan_artifacts(workdir)
        metadata = opf_metadata(workdir)
        after_hashes = {
            name: sha256_bytes((workdir / name).read_bytes())
            for name in PRESERVE_FILES
        }
        preserved_unchanged = {
            name: before_hashes[name] == after_hashes[name]
            for name in PRESERVE_FILES
        }
        final_status = "READY_FOR_KINDLE_PREVIEWER"
        if (
            artifact_scan
            or link_validation["broken_links"]
            or link_validation["duplicate_ids"]
            or not all(preserved_unchanged.values())
        ):
            final_status = "BLOCKED_NEEDS_FIX"
        write_epub(workdir, OUTPUT_EPUB)

    report = {
        "input_epub": str(INPUT_EPUB.relative_to(ROOT)),
        "input_sha256": sha256_file(INPUT_EPUB),
        "output_epub": str(OUTPUT_EPUB.relative_to(ROOT)),
        "output_sha256": sha256_file(OUTPUT_EPUB),
        "modified_timestamp_utc": modified_timestamp,
        "metadata": metadata,
        "preserved_files_unchanged": preserved_unchanged,
        "artifact_scan": artifact_scan,
        "link_validation": link_validation,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "final_status": final_status,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("REPUBLIC V06 READY EXPORT COMPLETE")
    print()
    print("Generated:")
    print(str(OUTPUT_EPUB.relative_to(ROOT)))
    print(str(REPORT_PATH.relative_to(ROOT)))
    print()
    print("Status:")
    print(final_status)
    if final_status != "READY_FOR_KINDLE_PREVIEWER":
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
