from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
BOOK_CODE = "book_0026"
LANG = "en"
BUILD_DIR = ROOT / "data" / "builds" / BOOK_CODE / LANG
FRONTMATTER_DIR = ROOT / "data" / "frontmatter" / BOOK_CODE / LANG
INPUT_BODY = BUILD_DIR / "dialogues_seneca_final_body.md"
COVER_PATH = ROOT / "data" / "covers" / BOOK_CODE / LANG / "cover.jpg"
SOURCE_MD = BUILD_DIR / "seneca_dialogues_v13_SOURCE.md"
OUTPUT_EPUB = BUILD_DIR / "seneca_dialogues_v13.epub"
REPORT_PATH = BUILD_DIR / "seneca_dialogues_v13_epub_report.json"
DOWNLOAD_DIR = Path("/home/periclesguara/Downloads/Dialogues\nSeneca")
DOWNLOAD_EPUB = DOWNLOAD_DIR / "seneca_dialogues_v13.epub"
DOWNLOAD_SOURCE = DOWNLOAD_DIR / "seneca_dialogues_v13_SOURCE.md"
DOWNLOAD_REPORT = DOWNLOAD_DIR / "seneca_dialogues_v13_epub_report.json"
DOWNLOAD_CANONICAL_MD = DOWNLOAD_DIR / "dialogues_seneca_v13_split_benefits_and_epilogue_pages.md"

TITLE = "Seneca’s Dialogues"
SUBTITLE = "Modern English Edition"
AUTHOR = "Lucius Annaeus Seneca"
PUBLISHER = "Rinobooks"

ID_RE = re.compile(r'\bid="([^"]+)"')
HREF_RE = re.compile(r'\bhref="#([^"]+)"')
CHAPTER_HEADING_RE = re.compile(r"^## Chapter \d{2} — .+$")
APHORISM_MARKER_RE = re.compile(r'^<p class="aphorism-number">(\d+)</p>$')
APHORISM_INLINE_RE = re.compile(r'<span class="aphorism-inline-number">\d+\.</span>')
GLOSSARY_HEADING = "# Glossary"

FRONTMATTER_ORDER = [
    "frontispiece.md",
    "copyright.md",
    "about_this_book.md",
    "about_contributor.md",
    "preface.md",
    "introduction.md",
]

EPILOGUE_TITLES = {
    "The Philosophical Lineage of Seneca",
    "Stoicism",
    "Attalus",
    "The Cynics",
    "Demetrius the Cynic",
    "Pythagorean Influence and the School of the Sextii",
    "Sotion",
    "Papirius Fabianus",
    "Socrates",
    "Plato",
    "Epicurus and the Epicureans",
    "The Roman Tradition",
    "Seneca’s Own Synthesis",
}

BENEFITS_TOC_LABELS = {
    "Chapter 12 — On Benefits I",
    "Chapter 13 — On Benefits II",
    "Chapter 14 — On Benefits III",
    "Chapter 15 — On Benefits IV",
    "Chapter 16 — On Benefits V",
    "Chapter 17 — On Benefits VI",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean_pagebreak_markers(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.strip() in {"::: pagebreak", ":::", r"\newpage"}:
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def read_frontmatter() -> tuple[str, list[str]]:
    sections: list[str] = []
    included: list[str] = []
    for name in FRONTMATTER_ORDER:
        path = FRONTMATTER_DIR / name
        if not path.exists():
            continue
        text = clean_pagebreak_markers(path.read_text(encoding="utf-8"))
        if not text:
            continue
        sections.append(text)
        included.append(name)
    return "\n\n".join(sections).strip(), included


def read_epilogue() -> str:
    path = FRONTMATTER_DIR / "epilogue.md"
    if not path.exists():
        return ""
    return clean_pagebreak_markers(path.read_text(encoding="utf-8"))


def split_body_and_glossary(text: str) -> tuple[str, str]:
    marker = "\n# Glossary\n"
    if marker not in text:
        raise ValueError("Missing '# Glossary' section in body input.")
    body, rest = text.split(marker, 1)
    return body.rstrip(), "# Glossary\n" + rest.strip()


def body_for_epub(body: str) -> tuple[str, dict[str, object]]:
    out: list[str] = []
    chapter_count = 0
    aphorism_count = 0
    skipped_chapter_page_tags = 0
    skipped_title_heading = False

    for raw in body.splitlines():
        stripped = raw.strip()
        if stripped == "# Seneca’s Dialogues":
            if skipped_title_heading:
                continue
            skipped_title_heading = True
            out.append(stripped)
            continue
        if stripped in {'<div class="chapter-page">', '<div class="subchapter-block">', "</div>"}:
            skipped_chapter_page_tags += 1
            continue
        if CHAPTER_HEADING_RE.match(stripped):
            chapter_count += 1
            out.append("")
            out.append(stripped)
            out.append("")
            out.append('<p class="chapter-text-start" aria-hidden="true">&#160;</p>')
            continue
        marker = APHORISM_MARKER_RE.match(stripped)
        if marker:
            aphorism_count += 1
            out.append("")
            out.append(f'<p class="aphorism-number"><strong>{marker.group(1)}</strong></p>')
            continue
        out.append(raw.rstrip())

    return "\n".join(out).strip(), {
        "chapter_count": chapter_count,
        "aphorism_count": aphorism_count,
        "skipped_chapter_page_tags": skipped_chapter_page_tags,
    }


def build_source_md() -> dict[str, object]:
    input_text = INPUT_BODY.read_text(encoding="utf-8")
    body, glossary = split_body_and_glossary(input_text)
    frontmatter, included_frontmatter = read_frontmatter()
    epub_body, body_report = body_for_epub(body)
    epilogue = read_epilogue()
    sections = [section for section in (frontmatter, epub_body, epilogue, glossary) if section]
    text = "\n\n".join(sections).strip() + "\n"
    SOURCE_MD.write_text(text, encoding="utf-8")
    return {
        "source_md": str(SOURCE_MD),
        "source_sha256": sha256_file(SOURCE_MD),
        "input_body": str(INPUT_BODY),
        "input_body_sha256": sha256_file(INPUT_BODY),
        "frontmatter_files": included_frontmatter,
        "has_epilogue": bool(epilogue),
        "body_report": body_report,
    }


def run_pandoc() -> dict[str, object]:
    if not COVER_PATH.exists():
        raise FileNotFoundError(COVER_PATH)
    css_path = BUILD_DIR / "epub_v13.css"
    css_path.write_text(
        "\n".join(
            [
                "body { margin: 0 4%; }",
                "p { text-indent: 0; margin: 0 0 1em 0; line-height: 1.38; }",
                "h1, h2, h3 { text-indent: 0; }",
                "h1 { break-before: page; page-break-before: always; }",
                "section.level2 { page-break-before: right; break-before: recto; break-before: right; }",
                "section.level2 > h2 { text-align: center; line-height: 1.25; margin: 0 auto; max-width: 90%; padding-top: 34vh; min-height: 92vh; box-sizing: border-box; page-break-after: always; break-after: page; }",
                "section.chapter-text-body, section.epilogue-section-body { page-break-before: always; break-before: page; }",
                ".chapter-text-start { display: block; page-break-before: always; break-before: page; height: 0; max-height: 0; margin: 0; padding: 0; line-height: 0; font-size: 0; color: transparent; }",
                ".chapter-text-start + .aphorism-number { margin-top: 0; }",
                ".chapter-page { page-break-before: always; break-before: page; page-break-after: always; break-after: page; text-align: center; margin-top: 30%; }",
                ".epilogue-section-page { page-break-before: always; break-before: page; page-break-after: always; break-after: page; text-align: center; margin-top: 30%; }",
                ".aphorism-inline-number { font-weight: bold; margin-right: 0.35em; }",
                ".aphorism-number { display: block; text-align: center; font-weight: bold; margin-top: 2.2em; margin-bottom: 1.25em; letter-spacing: 0.08em; }",
                ".aphorism-number strong { font-weight: 700; }",
                "p, li { orphans: 2; widows: 2; }",
                "sup { line-height: 0; font-size: 0.75em; }",
                "nav#toc ol { list-style-type: none; padding-left: 0; }",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    cmd = [
        "pandoc",
        str(SOURCE_MD),
        "--from=markdown+raw_html",
        "--to=epub3",
        "--toc",
        "--toc-depth=2",
        "--split-level=2",
        f"--resource-path={BUILD_DIR}:{FRONTMATTER_DIR}",
        f"--css={css_path}",
        f"--epub-cover-image={COVER_PATH}",
        f"--metadata=title:{TITLE}",
        f"--metadata=subtitle:{SUBTITLE}",
        f"--metadata=author:{AUTHOR}",
        f"--metadata=publisher:{PUBLISHER}",
        "--metadata=lang:en",
        "--metadata=language:en",
        "-o",
        str(OUTPUT_EPUB),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Pandoc failed.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    return {
        "cmd": cmd,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "epub": str(OUTPUT_EPUB),
        "epub_sha256": sha256_file(OUTPUT_EPUB),
    }


def write_epub_from_dir(workdir: Path, output: Path) -> None:
    mimetype = workdir / "mimetype"
    with zipfile.ZipFile(output, "w") as zf:
        zf.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)
        for path in sorted(workdir.rglob("*")):
            if path.is_dir() or path == mimetype:
                continue
            zf.write(path, path.relative_to(workdir).as_posix(), compress_type=zipfile.ZIP_DEFLATED)


def split_title_and_body_files(epub_path: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="seneca_v13_split_") as tmp:
        workdir = Path(tmp)
        with zipfile.ZipFile(epub_path) as zf:
            zf.extractall(workdir)

        epub_dir = workdir / "EPUB"
        text_dir = epub_dir / "text"
        split_files: list[dict[str, str]] = []
        for path in sorted(text_dir.glob("*.xhtml")):
            text = path.read_text(encoding="utf-8", errors="ignore")

            body_match = re.match(r"(?s)(.*?<body[^>]*>)(.*?)(</body>.*)", text)
            if not body_match:
                continue

            prefix, _body_content, suffix = body_match.groups()
            split_kind = ""
            section_match = re.search(
                r'(?s)(<section id="([^"]+)" class="level2">\s*<h2>Chapter\s+\d{2}\s+—.*?</h2>)(.*?)(</section>)',
                text,
            )
            if section_match:
                split_kind = "chapter"
                title_section, section_id, body_fragment, _section_close = section_match.groups()
                body_fragment = re.sub(
                    r'(?s)^\s*<p class="chapter-text-start"[^>]*>.*?</p>\s*',
                    "",
                    body_fragment,
                    count=1,
                ).strip()
                body_class = "chapter-text-body"
            else:
                epilogue_match = re.search(
                    r'(?s)(<section id="([^"]+)" class="level[12]">\s*<h([12])>(.*?)</h[12]>)(.*?)(</section>)',
                    text,
                )
                if not epilogue_match:
                    continue
                title_text_plain = re.sub(r"<[^>]+>", "", epilogue_match.group(4)).strip()
                if title_text_plain not in EPILOGUE_TITLES:
                    continue
                split_kind = "epilogue"
                title_section = epilogue_match.group(1)
                section_id = epilogue_match.group(2)
                body_fragment = epilogue_match.group(5).strip()
                body_class = "epilogue-section-body"

            body_fragment = re.sub(
                r'(?s)^\s*<p[^>]*>\s*(?:&nbsp;|&#160;|\s)*</p>\s*',
                "",
                body_fragment,
                count=1,
            ).strip()
            if not body_fragment:
                continue

            body_path = path.with_name(f"{path.stem}_body.xhtml")
            title_text = f"{prefix}\n{title_section}\n</section>\n{suffix}"
            body_title = body_path.name
            body_prefix = re.sub(r"<title>.*?</title>", f"<title>{body_title}</title>", prefix, count=1)
            body_text = (
                f'{body_prefix}\n<section id="{section_id}-body" class="{body_class}">\n'
                f"{body_fragment}\n</section>\n{suffix}"
            )
            path.write_text(title_text, encoding="utf-8")
            body_path.write_text(body_text, encoding="utf-8")
            split_files.append(
                {
                    "title_file": "EPUB/" + path.relative_to(epub_dir).as_posix(),
                    "body_file": "EPUB/" + body_path.relative_to(epub_dir).as_posix(),
                    "section_id": section_id,
                    "kind": split_kind,
                }
            )

        if split_files:
            update_opf_for_split_chapter_files(epub_dir / "content.opf", split_files)

        write_epub_from_dir(workdir, epub_path)
        return {
            "chapters_split": sum(1 for item in split_files if item["kind"] == "chapter"),
            "epilogue_sections_split": sum(1 for item in split_files if item["kind"] == "epilogue"),
            "split_files": split_files,
            "epub_sha256_after_split": sha256_file(epub_path),
        }


def update_opf_for_split_chapter_files(opf_path: Path, split_files: list[dict[str, str]]) -> None:
    ET.register_namespace("", "http://www.idpf.org/2007/opf")
    ns_uri = "http://www.idpf.org/2007/opf"
    ns = {"opf": ns_uri}
    tree = ET.parse(opf_path)
    root = tree.getroot()
    manifest = root.find("opf:manifest", ns)
    spine = root.find("opf:spine", ns)
    if manifest is None or spine is None:
        raise ValueError("Invalid EPUB OPF: missing manifest or spine.")

    href_to_id = {
        item.attrib.get("href", ""): item.attrib.get("id", "")
        for item in manifest.findall("opf:item", ns)
    }
    id_to_itemref = {
        itemref.attrib.get("idref", ""): index
        for index, itemref in enumerate(list(spine.findall("opf:itemref", ns)))
    }

    for entry in split_files:
        title_href = entry["title_file"].removeprefix("EPUB/")
        body_href = entry["body_file"].removeprefix("EPUB/")
        title_id = href_to_id.get(title_href)
        if not title_id:
            continue
        body_id = f"{title_id}-body"
        ET.SubElement(
            manifest,
            f"{{{ns_uri}}}item",
            {
                "id": body_id,
                "href": body_href,
                "media-type": "application/xhtml+xml",
            },
        )
        itemref = ET.Element(f"{{{ns_uri}}}itemref", {"idref": body_id})
        current_itemrefs = list(spine.findall("opf:itemref", ns))
        insert_at = id_to_itemref.get(title_id)
        if insert_at is None:
            spine.append(itemref)
            continue
        spine.insert(insert_at + 1, itemref)
        id_to_itemref = {
            existing.attrib.get("idref", ""): index
            for index, existing in enumerate(list(spine.findall("opf:itemref", ns)))
        }

    tree.write(opf_path, encoding="utf-8", xml_declaration=True)


def rewrite_glossary_links(epub_path: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="seneca_v13_epub_") as tmp:
        workdir = Path(tmp)
        with zipfile.ZipFile(epub_path) as zf:
            zf.extractall(workdir)

        xhtml_paths = sorted((workdir / "EPUB").rglob("*.xhtml"))
        glossary_rel = None
        glossary_ids: set[str] = set()
        ref_locations: dict[str, str] = {}
        for path in xhtml_paths:
            rel = path.relative_to(workdir / "EPUB").as_posix()
            text = path.read_text(encoding="utf-8", errors="ignore")
            for ref_id in re.findall(r'id="(ref-g\d{3})"', text):
                ref_locations[ref_id] = rel
            if 'id="g001"' in text and "G001" in text:
                glossary_rel = rel
                glossary_ids = {value for value in ID_RE.findall(text) if re.fullmatch(r"g\d{3}", value)}
        if glossary_rel is None:
            raise ValueError("Glossary XHTML file not found in EPUB.")

        rewritten = 0
        backlinks_rewritten = 0
        for path in xhtml_paths:
            rel = path.relative_to(workdir / "EPUB").as_posix()
            text = path.read_text(encoding="utf-8", errors="ignore")

            def repl(match: re.Match[str]) -> str:
                nonlocal rewritten
                target = match.group(1)
                if rel != glossary_rel and target in glossary_ids:
                    rewritten += 1
                    return f'href="{Path(glossary_rel).name}#{target}"'
                return match.group(0)

            new_text = HREF_RE.sub(repl, text)
            if rel == glossary_rel:
                def backlink_repl(match: re.Match[str]) -> str:
                    nonlocal backlinks_rewritten
                    ref_target = match.group(1)
                    ref_rel = ref_locations.get(ref_target)
                    if ref_rel:
                        backlinks_rewritten += 1
                        return f'href="{Path(ref_rel).name}#{ref_target}"'
                    return match.group(0)

                new_text = re.sub(r'href="#(ref-g\d{3})"', backlink_repl, new_text)
            if new_text != text:
                path.write_text(new_text, encoding="utf-8")

        write_epub_from_dir(workdir, epub_path)
        return {
            "glossary_file": f"EPUB/{glossary_rel}",
            "glossary_ids": len(glossary_ids),
            "body_ref_ids": len(ref_locations),
            "body_glossary_links_rewritten": rewritten,
            "glossary_backlinks_rewritten": backlinks_rewritten,
            "epub_sha256_after_rewrite": sha256_file(epub_path),
        }


def validate_epub(epub_path: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="seneca_v13_validate_") as tmp:
        workdir = Path(tmp)
        with zipfile.ZipFile(epub_path) as zf:
            names = set(zf.namelist())
            zf.extractall(workdir)

        xhtml_paths = sorted((workdir / "EPUB").rglob("*.xhtml"))
        ids_by_file: dict[str, set[str]] = {}
        hrefs: list[tuple[str, str]] = []
        toc_labels: list[str] = []
        glossary_file = ""
        cover_present = any("cover" in name.lower() and name.lower().endswith((".jpg", ".jpeg", ".png")) for name in names)
        chapter_headings = 0
        source_heading_residue = 0
        aphorism_markers = 0
        subchapter_residue = 0
        on_benefits_counts: dict[str, int] = {}
        epilogue_pages_with_body_text: list[dict[str, str]] = []
        endnote_residue = 0
        for path in xhtml_paths:
            rel = "EPUB/" + path.relative_to(workdir / "EPUB").as_posix()
            text = path.read_text(encoding="utf-8", errors="ignore")
            ids_by_file[rel] = set(ID_RE.findall(text))
            if 'id="g001"' in text:
                glossary_file = rel
            hrefs.extend((rel, href) for href in re.findall(r'href="([^"]+)"', text))
            if rel.endswith("nav.xhtml"):
                toc_labels = [re.sub(r"<[^>]+>", "", label).strip() for label in re.findall(r"<a [^>]*>(.*?)</a>", text)]
            chapter_headings += len(re.findall(r"<h2[^>]*>\s*Chapter\s+\d{2}\s+—", text))
            source_heading_residue += len(re.findall(r"<h[23][^>]*>\s*(?:Part|Book|Section|Aphorism)\b", text))
            aphorism_markers += len(APHORISM_INLINE_RE.findall(text))
            subchapter_residue += text.count('class="subchapter-number"')
            subchapter_residue += text.count('class="subchapter-block"')
            is_glossary_file = 'id="g001"' in text and "G001" in text
            if not is_glossary_file and any(
                pattern in text
                for pattern in ("# Endnotes", "Endnotes", "↩︎", "J. E. B. Mayor", "Koch declares", "Gertz reads", "Lipsius", "La Grange")
            ):
                endnote_residue += 1
            benefits_match = re.search(r'<section id="chapter-(1[2-7])-[^"]*-body"', text)
            if benefits_match:
                on_benefits_counts[benefits_match.group(1)] = len(APHORISM_INLINE_RE.findall(text))
            epilogue_match = re.search(
                r'(?s)<section id="([^"]+)" class="level[12]">\s*<h[12]>(.*?)</h[12]>(.*?)</section>',
                text,
            )
            if epilogue_match:
                title = re.sub(r"<[^>]+>", "", epilogue_match.group(2)).strip()
                body_fragment = epilogue_match.group(3).strip()
                if title in EPILOGUE_TITLES and re.search(r"<p\b|<ul\b|<ol\b|<blockquote\b", body_fragment):
                    epilogue_pages_with_body_text.append({"file": rel, "title": title})

        broken: list[dict[str, str]] = []
        for source_rel, href in hrefs:
            if href.startswith(("http:", "https:", "mailto:")):
                continue
            if "#" in href:
                file_part, frag = href.split("#", 1)
            else:
                file_part, frag = href, ""
            if not file_part:
                target_rel = source_rel
            else:
                source_dir = posixpath.dirname(source_rel)
                target_rel = posixpath.normpath(posixpath.join(source_dir, file_part))
            if target_rel not in names and target_rel not in ids_by_file:
                broken.append({"source": source_rel, "href": href, "reason": "missing_file"})
                continue
            if frag and frag not in ids_by_file.get(target_rel, set()):
                broken.append({"source": source_rel, "href": href, "reason": "missing_fragment"})

        opf_path = workdir / "EPUB" / "content.opf"
        spine_count = 0
        if opf_path.exists():
            ns = {"opf": "http://www.idpf.org/2007/opf"}
            root = ET.fromstring(opf_path.read_text(encoding="utf-8"))
            spine = root.find("opf:spine", ns)
            spine_count = len(spine.findall("opf:itemref", ns)) if spine is not None else 0

        toc_chapter_entries = sum(1 for label in toc_labels if re.match(r"Chapter\s+\d{2}\s+—", label))
        toc_has_glossary = any(label == "Glossary" for label in toc_labels)
        missing_benefits_toc = sorted(BENEFITS_TOC_LABELS.difference(toc_labels))
        oversized_benefits = [
            {"chapter": chapter, "aphorisms": count}
            for chapter, count in sorted(on_benefits_counts.items())
            if count > 125
        ]
        return {
            "cover_present": cover_present,
            "xhtml_files": len(xhtml_paths),
            "spine_count": spine_count,
            "chapter_headings": chapter_headings,
            "source_heading_residue": source_heading_residue,
            "aphorism_markers": aphorism_markers,
            "subchapter_residue": subchapter_residue,
            "on_benefits_counts": on_benefits_counts,
            "on_benefits_total_aphorisms": sum(on_benefits_counts.values()),
            "oversized_on_benefits_chapters": oversized_benefits,
            "missing_benefits_toc": missing_benefits_toc,
            "epilogue_pages_with_body_text": epilogue_pages_with_body_text,
            "endnotes_remaining": bool(endnote_residue),
            "toc_labels": toc_labels,
            "toc_has_glossary": toc_has_glossary,
            "toc_chapter_entries": toc_chapter_entries,
            "glossary_file": glossary_file,
            "broken_links": broken,
            "validation_passed": cover_present
            and chapter_headings == 17
            and toc_chapter_entries == 17
            and not missing_benefits_toc
            and toc_has_glossary
            and source_heading_residue == 0
            and aphorism_markers == 1714
            and subchapter_residue == 0
            and sum(on_benefits_counts.values()) == 617
            and not oversized_benefits
            and not epilogue_pages_with_body_text
            and not endnote_residue
            and not broken
            and bool(glossary_file),
        }


def main() -> int:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    source_report = build_source_md()
    pandoc_report = run_pandoc()
    split_report = split_title_and_body_files(OUTPUT_EPUB)
    rewrite_report = rewrite_glossary_links(OUTPUT_EPUB)
    validation = validate_epub(OUTPUT_EPUB)

    shutil.copy2(OUTPUT_EPUB, DOWNLOAD_EPUB)
    shutil.copy2(SOURCE_MD, DOWNLOAD_SOURCE)
    shutil.copy2(INPUT_BODY, DOWNLOAD_CANONICAL_MD)

    report = {
        "book_code": BOOK_CODE,
        "language": LANG,
        "input_body": str(INPUT_BODY),
        "cover": str(COVER_PATH),
        "source": source_report,
        "pandoc": pandoc_report,
        "title_body_split": split_report,
        "glossary_link_rewrite": rewrite_report,
        "validation": validation,
        "outputs": {
            "epub": str(OUTPUT_EPUB),
            "source_md": str(SOURCE_MD),
            "report": str(REPORT_PATH),
            "download_epub": str(DOWNLOAD_EPUB),
            "download_source_md": str(DOWNLOAD_SOURCE),
            "download_canonical_md": str(DOWNLOAD_CANONICAL_MD),
            "download_report": str(DOWNLOAD_REPORT),
        },
        "hashes": {
            "epub": sha256_file(OUTPUT_EPUB),
            "download_epub": sha256_file(DOWNLOAD_EPUB),
            "source_md": sha256_file(SOURCE_MD),
            "download_source_md": sha256_file(DOWNLOAD_SOURCE),
            "canonical_md": sha256_file(INPUT_BODY),
            "download_canonical_md": sha256_file(DOWNLOAD_CANONICAL_MD),
        },
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.copy2(REPORT_PATH, DOWNLOAD_REPORT)
    print(
        json.dumps(
            {
                "epub": str(OUTPUT_EPUB),
                "download_epub": str(DOWNLOAD_EPUB),
                "validation_passed": validation["validation_passed"],
                "chapter_headings": validation["chapter_headings"],
                "toc_chapter_entries": validation["toc_chapter_entries"],
                "toc_has_glossary": validation["toc_has_glossary"],
                "aphorism_markers": validation["aphorism_markers"],
                "subchapter_residue": validation["subchapter_residue"],
                "on_benefits_total_aphorisms": validation["on_benefits_total_aphorisms"],
                "epilogue_pages_with_body_text": len(validation["epilogue_pages_with_body_text"]),
                "broken_links": len(validation["broken_links"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if validation["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
