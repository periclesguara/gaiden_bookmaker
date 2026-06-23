#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = ROOT / "data" / "builds" / "book_0023" / "en"
FRONTMATTER_DIR = ROOT / "data" / "frontmatter" / "book_0023" / "en"
DEFAULT_SOURCE = BUILD_DIR / "dracula_v12_ultra_surgical_cleanup.epub"
DEFAULT_OUTPUT = BUILD_DIR / "dracula_v13_complete_edition.epub"
DEFAULT_MARKDOWN = BUILD_DIR / "kdp_merged_v13.md"
DEFAULT_BODY = BUILD_DIR / "dracula_body_v13.md"
IMAGE_OVERRIDE_CHAPTERS = (3, 4, 12, 21, 27)

SECTION_SPECS = (
    ("ch001.xhtml", "frontispiece.md", "Frontispiece", "frontmatter"),
    ("ch002.xhtml", "copyright.md", "Copyright", "frontmatter"),
    ("ch003.xhtml", "about_this_book.md", "About This Book", "frontmatter"),
    ("ch004.xhtml", "about_contributor.md", "About the Author", "frontmatter"),
    ("introduction.xhtml", "introduction.md", "Introduction", "frontmatter"),
    ("epilogue.xhtml", "epilogue.md", "Epilogue", "backmatter"),
)

TOC_ITEMS = [
    ("text/ch001.xhtml#frontispiece", "Frontispiece"),
    ("text/ch002.xhtml#copyright", "Copyright"),
    ("text/ch003.xhtml#about-this-book", "About This Book"),
    ("text/ch004.xhtml#about-the-author", "About the Author"),
    ("text/introduction.xhtml#introduction", "Introduction"),
    *[(f"text/ch{chapter + 4:03d}.xhtml#chapter-{chapter}", f"Chapter {chapter}") for chapter in range(1, 28)],
    ("text/epilogue.xhtml#epilogue", "Epilogue"),
]


def run(*args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        args,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def clean_frontmatter_markdown(text: str) -> str:
    return re.sub(r"(?m)^:::\s*pagebreak\s*$", "", text).strip() + "\n"


def markdown_fragment(text: str) -> str:
    return run(
        "pandoc",
        "--from=markdown",
        "--to=html5",
        "--section-divs",
        input_text=text,
    ).strip()


def xhtml_document(markdown: str, title: str, matter: str) -> str:
    fragment = markdown_fragment(clean_frontmatter_markdown(markdown))
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>

<html lang="en" xml:lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
<meta charset="utf-8"/>
<meta content="pandoc" name="generator"/>
<title>{html.escape(title)}</title>
<style>
</style>
<link href="../styles/stylesheet1.css" rel="stylesheet" type="text/css"/>
</head>
<body epub:type="{matter}">
{fragment}
</body>
</html>
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one {label}; found {count}")
    return text.replace(old, new)


def clean_body_xhtml(files_dir: Path) -> None:
    replacements: dict[str, list[tuple[str, str, str]]] = {
        "ch005.xhtml": [
            (
                '<p><strong>Entry 01 - <em>(Kept in shorthand.)</em></strong></p>',
                '<p><em>(Kept in shorthand.)</em></p>',
                "Entry 01",
            ),
            (
                "(Mem., I must ask the Count about these superstitions)</p>",
                "(Mem., I must ask the Count about these superstitions.)</p>",
                "Mem punctuation",
            ),
        ],
        "ch009.xhtml": [
            (
                "Bless me in your prayers; and, Mina, pray for my happiness. “Lucy. “P.S.—I need not tell you this is a secret. Good night again. “”</p>",
                "Bless me in your prayers; and, Mina, pray for my happiness.</p>\n<p>Lucy.</p>\n<p>P.S. — I need not tell you this is a secret. Good night again.</p>",
                "Lucy first signature",
            ),
            (
                "and I don’t wish to tell of the number three until I can tell it happily. “Ever your loving “Lucy. “P.S.—Oh, about number Three—I needn’t tell you of number Three, need I? Besides, it was all so confused; it seemed only a moment between his coming into the room and his arms being round me, and he was kissing me. I am very, very happy, and I don’t know what I have done to deserve it. I must only try in the future to show that I am not ungrateful to God for all His goodness to me in sending me such a lover, such a husband, and such a friend. “Goodbye.”</p>",
                "and I don’t wish to tell of the number three until I can tell it happily.</p>\n<p>Ever your loving,</p>\n<p>Lucy.</p>\n<p>P.S. — Oh, about number Three—I needn’t tell you of number Three, need I? Besides, it was all so confused; it seemed only a moment between his coming into the room and his arms being round me, and he was kissing me. I am very, very happy, and I don’t know what I have done to deserve it. I must only try in the future to show that I am not ungrateful to God for all His goodness to me in sending me such a lover, such a husband, and such a friend.</p>\n<p>Goodbye.</p>",
                "Lucy second signature",
            ),
        ],
        "ch010.xhtml": [
            ("<p>p.m.—I have visited him again", "<p>10 p.m.—I have visited him again", "10 p.m."),
            ("<p>a.m.—The attendant has just been to me", "<p>11 a.m.—The attendant has just been to me", "11 a.m."),
            ("<p>p.m.—I gave Renfield a strong opiate tonight", "<p>11 p.m.—I gave Renfield a strong opiate tonight", "11 p.m."),
        ],
        "ch013.xhtml": [
            (
                "<p>“Whitby, 30 August. “My dearest Mina— “Oceans of love",
                "<p>Whitby, 30 August.</p>\n<p>My dearest Mina—</p>\n<p>Oceans of love",
                "Lucy letter heading",
            ),
            (
                "So no more just at present from your loving “Lucy. “P.S.—Mother sends her love. She seems better, poor dear. “P.P.S.—We are to be married on 28 September.”</p>",
                "So no more just at present from your loving</p>\n<p>Lucy.</p>\n<p>P.S. — Mother sends her love. She seems better, poor dear.</p>\n<p>P.P.S. — We are to be married on 28 September.</p>",
                "Lucy third signature",
            ),
        ],
        "ch017.xhtml": [
            (
                '<p><strong>Entry 02 - Extra Special. The Hampstead Horror. Another Child Injured. The “Bloofer Lady.”</strong></p>',
                '<p><strong>Extra Special. The Hampstead Horror. Another Child Injured. The “Bloofer Lady.”</strong></p>',
                "Entry 02",
            ),
        ],
        "ch021.xhtml": [
            (
                '<p>“Am coming up by train. Jonathan at Whitby. Important news.—</p>\n<p><strong>Entry 03 - Mina Harker</strong></p>',
                '<p>“Am coming up by train. Jonathan at Whitby. Important news.—Mina Harker.”</p>',
                "Entry 03",
            ),
        ],
        "ch029.xhtml": [
            (
                '<p><strong>Entry 04 - Rufus Smith, London, to Lord Godalming, care of</strong></p>\n<p>H.B.M. Vice-Consul, Varna. “Czarina Catherine reported entering Galatz at one o’clock today.”</p>',
                '<p><strong>Rufus Smith, London, to Lord Godalming, care of H.B.M. Vice-Consul, Varna.</strong></p>\n<p>“Czarina Catherine reported entering Galatz at one o’clock today.”</p>',
                "Entry 04",
            ),
        ],
    }
    for name, operations in replacements.items():
        path = files_dir / name
        text = path.read_text(encoding="utf-8")
        for old, new, label in operations:
            text = replace_once(text, old, new, label)
        path.write_text(text, encoding="utf-8")


def build_nav() -> str:
    items = "".join(
        f'<li id="toc-li-{index}"><a href="{html.escape(href)}">{html.escape(label)}</a></li>'
        for index, (href, label) in enumerate(TOC_ITEMS, start=1)
    )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>

<html lang="en" xml:lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
<meta charset="utf-8"/>
<meta content="Gaiden BookMaker" name="generator"/>
<title>Dracula</title>
<link href="styles/stylesheet1.css" rel="stylesheet" type="text/css"/>
</head>
<body epub:type="frontmatter">
<nav epub:type="toc" id="toc" role="doc-toc"><h1 id="toc-title">Dracula</h1><ol class="toc">{items}</ol></nav>
<nav epub:type="landmarks" hidden="hidden" id="landmarks"><ol>
<li><a epub:type="cover" href="text/cover.xhtml">Cover</a></li>
<li><a epub:type="toc" href="#toc">Table of Contents</a></li>
<li><a epub:type="bodymatter" href="text/ch005.xhtml#chapter-1">Beginning of the novel</a></li>
</ol></nav>
</body>
</html>
'''


def build_ncx(identifier: str) -> str:
    points = []
    for index, (href, label) in enumerate(TOC_ITEMS, start=1):
        points.append(
            f'''    <navPoint id="navPoint-{index}" playOrder="{index}">
      <navLabel><text>{html.escape(label)}</text></navLabel>
      <content src="{html.escape(href)}" />
    </navPoint>'''
        )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<ncx version="2005-1" xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <head>
    <meta name="dtb:uid" content="{identifier}" />
    <meta name="dtb:depth" content="1" />
    <meta name="dtb:totalPageCount" content="0" />
    <meta name="dtb:maxPageNumber" content="0" />
    <meta name="cover" content="cover_jpg" />
  </head>
  <docTitle><text>Dracula</text></docTitle>
  <navMap>
{chr(10).join(points)}
  </navMap>
</ncx>
'''


def update_opf(opf: str, identifier: str, timestamp: str) -> str:
    opf = re.sub(
        r'(<dc:identifier id="epub-id-1">).*?(</dc:identifier>)',
        rf'\g<1>{identifier}\g<2>',
        opf,
        count=1,
    )
    opf = re.sub(
        r'(<dc:date id="epub-date">).*?(</dc:date>)',
        rf'\g<1>{timestamp}\g<2>',
        opf,
        count=1,
    )
    opf = re.sub(
        r'(<meta property="dcterms:modified">).*?(</meta>)',
        rf'\g<1>{timestamp}\g<2>',
        opf,
        count=1,
    )
    manifest_anchor = '    <item id="ch031_xhtml" href="text/ch031.xhtml" media-type="application/xhtml+xml" />'
    manifest_extra = (
        manifest_anchor
        + '\n    <item id="introduction_xhtml" href="text/introduction.xhtml" media-type="application/xhtml+xml" />'
        + '\n    <item id="epilogue_xhtml" href="text/epilogue.xhtml" media-type="application/xhtml+xml" />'
    )
    opf = replace_once(opf, manifest_anchor, manifest_extra, "OPF manifest anchor")
    opf = replace_once(
        opf,
        '    <itemref idref="ch004_xhtml" />',
        '    <itemref idref="ch004_xhtml" />\n    <itemref idref="introduction_xhtml" />',
        "OPF introduction spine anchor",
    )
    opf = replace_once(
        opf,
        '    <itemref idref="ch031_xhtml" />',
        '    <itemref idref="ch031_xhtml" />\n    <itemref idref="epilogue_xhtml" />',
        "OPF epilogue spine anchor",
    )
    return opf


def clean_markdown_body(body: str) -> str:
    operations = [
        ("**Entry 01 - *(Kept in shorthand.)***", "*(Kept in shorthand.)*", "MD Entry 01"),
        ("(Mem., I must ask the Count about these superstitions)", "(Mem., I must ask the Count about these superstitions.)", "MD memo"),
        ("**Entry 02 - Extra Special.", "**Extra Special.", "MD Entry 02"),
        ("“Am coming up by train. Jonathan at Whitby. Important news.—\n\n\n**Entry 03 - Mina Harker**", "“Am coming up by train. Jonathan at Whitby. Important news.—Mina Harker.”", "MD Entry 03"),
        ("**Entry 04 - Rufus Smith, London, to Lord Godalming, care of**\n\nH.B.M. Vice-Consul, Varna. “Czarina Catherine reported entering Galatz at one o’clock today.”", "**Rufus Smith, London, to Lord Godalming, care of H.B.M. Vice-Consul, Varna.**\n\n“Czarina Catherine reported entering Galatz at one o’clock today.”", "MD Entry 04"),
        ("\np.m.—I have visited him again", "\n10 p.m.—I have visited him again", "MD 10 p.m."),
        ("\na.m.—The attendant has just been to me", "\n11 a.m.—The attendant has just been to me", "MD 11 a.m."),
        ("\np.m.—I gave Renfield a strong opiate tonight", "\n11 p.m.—I gave Renfield a strong opiate tonight", "MD 11 p.m."),
        ("Bless me in your prayers; and, Mina, pray for my happiness. “Lucy. “P.S.—I need not tell you this is a secret. Good night again. “”", "Bless me in your prayers; and, Mina, pray for my happiness.\n\nLucy.\n\nP.S. — I need not tell you this is a secret. Good night again.", "MD Lucy first signature"),
        ("and I don’t wish to tell of the number three until I can tell it happily. “Ever your loving “Lucy. “P.S.—Oh, about number Three—I needn’t tell you of number Three, need I? Besides, it was all so confused; it seemed only a moment between his coming into the room and his arms being round me, and he was kissing me. I am very, very happy, and I don’t know what I have done to deserve it. I must only try in the future to show that I am not ungrateful to God for all His goodness to me in sending me such a lover, such a husband, and such a friend. “Goodbye.”", "and I don’t wish to tell of the number three until I can tell it happily.\n\nEver your loving,\n\nLucy.\n\nP.S. — Oh, about number Three—I needn’t tell you of number Three, need I? Besides, it was all so confused; it seemed only a moment between his coming into the room and his arms being round me, and he was kissing me. I am very, very happy, and I don’t know what I have done to deserve it. I must only try in the future to show that I am not ungrateful to God for all His goodness to me in sending me such a lover, such a husband, and such a friend.\n\nGoodbye.", "MD Lucy second signature"),
        ("“Whitby, 30 August. “My dearest Mina— “Oceans of love", "Whitby, 30 August.\n\nMy dearest Mina—\n\nOceans of love", "MD Lucy letter heading"),
        ("So no more just at present from your loving “Lucy. “P.S.—Mother sends her love. She seems better, poor dear. “P.P.S.—We are to be married on 28 September.”", "So no more just at present from your loving\n\nLucy.\n\nP.S. — Mother sends her love. She seems better, poor dear.\n\nP.P.S. — We are to be married on 28 September.", "MD Lucy third signature"),
    ]
    for old, new, label in operations:
        body = replace_once(body, old, new, label)
    return body


def write_v13_markdown(path: Path, body_path: Path) -> None:
    v12 = (BUILD_DIR / "kdp_merged_v12.md").read_text(encoding="utf-8")
    marker = "# Chapter 1\n"
    if marker not in v12:
        raise RuntimeError("Chapter 1 marker not found in v12 markdown")
    body = marker + v12.split(marker, 1)[1]
    body = clean_markdown_body(body).strip()
    front_names = ("frontispiece.md", "copyright.md", "about_this_book.md", "about_contributor.md", "introduction.md")
    front = "\n\n".join(clean_frontmatter_markdown((FRONTMATTER_DIR / name).read_text(encoding="utf-8")).strip() for name in front_names)
    epilogue = clean_frontmatter_markdown((FRONTMATTER_DIR / "epilogue.md").read_text(encoding="utf-8")).strip()
    path.write_text(f"{front}\n\n{body}\n\n{epilogue}\n", encoding="utf-8")
    body_path.write_text(f"{body}\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--body-output", type=Path, default=DEFAULT_BODY)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.source, args.output)

    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    identifier = f"urn:uuid:{uuid.uuid4()}"

    with tempfile.TemporaryDirectory(prefix="dracula-v13-") as temp_name:
        temp = Path(temp_name)
        with zipfile.ZipFile(args.source) as source_zip:
            needed = [
                "EPUB/content.opf",
                "EPUB/nav.xhtml",
                "EPUB/toc.ncx",
                "EPUB/text/cover.xhtml",
                *[f"EPUB/text/ch{number:03d}.xhtml" for number in range(1, 32)],
            ]
            for member in needed:
                source_zip.extract(member, temp)

        text_dir = temp / "EPUB" / "text"
        clean_body_xhtml(text_dir)
        cover_path = text_dir / "cover.xhtml"
        cover = cover_path.read_text(encoding="utf-8")
        cover = replace_once(cover, "preserveaspectratio=", "preserveAspectRatio=", "cover preserveAspectRatio")
        cover = replace_once(cover, "viewbox=", "viewBox=", "cover viewBox")
        cover_path.write_text(cover, encoding="utf-8")

        for output_name, source_name, title, matter in SECTION_SPECS:
            markdown = (FRONTMATTER_DIR / source_name).read_text(encoding="utf-8")
            (text_dir / output_name).write_text(xhtml_document(markdown, title, matter), encoding="utf-8")

        media_dir = temp / "EPUB" / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        for chapter in IMAGE_OVERRIDE_CHAPTERS:
            source_image = BUILD_DIR / "assets" / "images" / f"{chapter:02d}.jpg"
            target_image = media_dir / f"file{chapter - 1}.jpg"
            shutil.copy2(source_image, target_image)

        (temp / "EPUB" / "nav.xhtml").write_text(build_nav(), encoding="utf-8")
        (temp / "EPUB" / "toc.ncx").write_text(build_ncx(identifier), encoding="utf-8")
        opf_path = temp / "EPUB" / "content.opf"
        opf_path.write_text(update_opf(opf_path.read_text(encoding="utf-8"), identifier, timestamp), encoding="utf-8")

        members = [
            "EPUB/content.opf",
            "EPUB/nav.xhtml",
            "EPUB/toc.ncx",
            "EPUB/text/cover.xhtml",
            *[f"EPUB/text/{name}" for name, *_ in SECTION_SPECS],
            *[f"EPUB/text/{name}" for name in ("ch005.xhtml", "ch009.xhtml", "ch010.xhtml", "ch013.xhtml", "ch017.xhtml", "ch021.xhtml", "ch029.xhtml")],
            *[f"EPUB/media/file{chapter - 1}.jpg" for chapter in IMAGE_OVERRIDE_CHAPTERS],
        ]
        unique_members = list(dict.fromkeys(members))
        result = subprocess.run(["zip", "-q", "-u", str(args.output), *unique_members], cwd=temp, check=False)
        if result.returncode:
            raise RuntimeError(f"zip update failed with exit code {result.returncode}")

    write_v13_markdown(args.markdown_output, args.body_output)
    print(args.output)
    print(args.markdown_output)
    print(args.body_output)


if __name__ == "__main__":
    main()
