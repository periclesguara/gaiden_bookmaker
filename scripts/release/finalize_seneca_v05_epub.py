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
INPUT_BODY = BUILD_DIR / "dialogues_seneca_final_body.md"
COVER_PATH = ROOT / "data" / "covers" / BOOK_CODE / LANG / "cover.jpg"
SOURCE_MD = BUILD_DIR / "seneca_dialogues_v05_SOURCE.md"
OUTPUT_EPUB = BUILD_DIR / "seneca_dialogues_v05.epub"
REPORT_PATH = BUILD_DIR / "seneca_dialogues_v05_epub_report.json"
DOWNLOAD_DIR = Path("/home/periclesguara/Downloads/Dialogues\nSeneca")
DOWNLOAD_EPUB = DOWNLOAD_DIR / "seneca_dialogues_v05.epub"
DOWNLOAD_SOURCE = DOWNLOAD_DIR / "seneca_dialogues_v05_SOURCE.md"
DOWNLOAD_REPORT = DOWNLOAD_DIR / "seneca_dialogues_v05_epub_report.json"
DOWNLOAD_V05_MD = DOWNLOAD_DIR / "dialogues_seneca_v05_us_english_spelling.md"

TITLE = "Seneca’s Dialogues"
SUBTITLE = "Modern English Edition"
AUTHOR = "Lucius Annaeus Seneca"
PUBLISHER = "Rinobooks"

ID_RE = re.compile(r'\bid="([^"]+)"')
HREF_RE = re.compile(r'\bhref="#([^"]+)"')


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_source_md() -> dict[str, object]:
    text = INPUT_BODY.read_text(encoding="utf-8").strip() + "\n"
    SOURCE_MD.write_text(text, encoding="utf-8")
    return {
        "source_md": str(SOURCE_MD),
        "source_sha256": sha256_file(SOURCE_MD),
        "input_body": str(INPUT_BODY),
        "input_body_sha256": sha256_file(INPUT_BODY),
    }


def run_pandoc() -> dict[str, object]:
    if not COVER_PATH.exists():
        raise FileNotFoundError(COVER_PATH)
    css_path = BUILD_DIR / "epub_v05.css"
    css_path.write_text(
        "\n".join(
            [
                "body { margin: 0 4%; }",
                "p { text-indent: 0; margin: 0 0 0.95em 0; line-height: 1.35; }",
                "h1, h2, h3 { text-indent: 0; }",
                "h1 { break-before: page; page-break-before: always; }",
                "h2 { text-align: center; line-height: 1.25; margin: 0; }",
                "section.chapter-page, div.chapter-page { page-break-before: right; break-before: recto; break-before: right; page-break-after: always; break-after: page; text-align: center; min-height: 92vh; padding-top: 34vh; box-sizing: border-box; }",
                "section.chapter-page + p.aphorism-number, div.chapter-page + p.aphorism-number { page-break-before: always; break-before: page; }",
                ".aphorism-number { text-align: center; font-weight: bold; margin-top: 1.9em; margin-bottom: 1.15em; letter-spacing: 0.08em; }",
                "section.chapter-page h2, div.chapter-page h2 { margin: 0 auto; max-width: 90%; }",
                "section.level2:not(.chapter-page) { page-break-before: auto; break-before: auto; }",
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
        "--split-level=1",
        f"--resource-path={BUILD_DIR}",
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


def rewrite_glossary_links(epub_path: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="seneca_v05_epub_") as tmp:
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
    with tempfile.TemporaryDirectory(prefix="seneca_v05_validate_") as tmp:
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
            aphorism_markers += text.count('class="aphorism-number"')

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
        return {
            "cover_present": cover_present,
            "xhtml_files": len(xhtml_paths),
            "spine_count": spine_count,
            "chapter_headings": chapter_headings,
            "source_heading_residue": source_heading_residue,
            "aphorism_markers": aphorism_markers,
            "toc_labels": toc_labels,
            "toc_has_glossary": toc_has_glossary,
            "toc_chapter_entries": toc_chapter_entries,
            "glossary_file": glossary_file,
            "broken_links": broken,
            "validation_passed": cover_present
            and chapter_headings == 12
            and toc_chapter_entries == 12
            and toc_has_glossary
            and source_heading_residue == 0
            and aphorism_markers == 64
            and not broken
            and bool(glossary_file),
        }


def main() -> int:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    source_report = build_source_md()
    pandoc_report = run_pandoc()
    rewrite_report = rewrite_glossary_links(OUTPUT_EPUB)
    validation = validate_epub(OUTPUT_EPUB)

    shutil.copy2(OUTPUT_EPUB, DOWNLOAD_EPUB)
    shutil.copy2(SOURCE_MD, DOWNLOAD_SOURCE)
    shutil.copy2(INPUT_BODY, DOWNLOAD_V05_MD)

    report = {
        "book_code": BOOK_CODE,
        "language": LANG,
        "input_body": str(INPUT_BODY),
        "cover": str(COVER_PATH),
        "source": source_report,
        "pandoc": pandoc_report,
        "glossary_link_rewrite": rewrite_report,
        "validation": validation,
        "outputs": {
            "epub": str(OUTPUT_EPUB),
            "source_md": str(SOURCE_MD),
            "report": str(REPORT_PATH),
            "download_epub": str(DOWNLOAD_EPUB),
            "download_source_md": str(DOWNLOAD_SOURCE),
            "download_v05_md": str(DOWNLOAD_V05_MD),
            "download_report": str(DOWNLOAD_REPORT),
        },
        "hashes": {
            "epub": sha256_file(OUTPUT_EPUB),
            "download_epub": sha256_file(DOWNLOAD_EPUB),
            "source_md": sha256_file(SOURCE_MD),
            "download_source_md": sha256_file(DOWNLOAD_SOURCE),
            "v05_md": sha256_file(INPUT_BODY),
            "download_v05_md": sha256_file(DOWNLOAD_V05_MD),
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
                "broken_links": len(validation["broken_links"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if validation["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
