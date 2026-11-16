"""Lossless text-first structure adapter. Unsupported markup fails closed."""
import re
from pathlib import PurePosixPath

from .latex import escape_latex
from .specs import PrintSpec


def _frontispiece_page(lines: list[str]) -> str:
    values = [line.strip() for line in lines if line.strip()]
    if not values:
        return ""
    rendered = []
    for index, value in enumerate(values):
        clean = re.sub(r"^#+\s*", "", value).strip("*")
        if index == 0:
            rendered.append(r"{\large\bfseries " + escape_latex(clean) + r"}\par")
        elif index == 1:
            rendered.append(r"\vspace{0.20in}{\Huge\bfseries " + escape_latex(clean) + r"}\par")
        elif value.startswith("*") and value.endswith("*"):
            rendered.append(r"\vspace{0.15in}{\large\itshape " + escape_latex(clean) + r"}\par")
        elif value.isupper() or value.startswith("Rio de Janeiro"):
            rendered.append(r"\vspace{0.08in}{\small " + escape_latex(clean) + r"}\par")
        else:
            rendered.append(r"\vspace{0.03in}{\small " + escape_latex(clean) + r"}\par")
    return "\\frontispiecepage\n" + "\n".join(rendered) + "\n\\closefrontispiecepage\n"


def _safe_image_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise ValueError("invalid print illustration path")
    return "/".join(path.parts)


def _end_label(language_code: str = "en") -> str:
    normalized = (language_code or "en").strip().lower().replace("_", "-")
    return "Fim" if normalized == "pt" or normalized.startswith("pt-") else "The End"


def _chapter_number(heading_text: str):
    match = re.fullmatch(
        r"(?:Chapter|Cap[ií]tulo)\s+0*(\d+)(?:\s*[-.:—–].*)?",
        heading_text,
        flags=re.IGNORECASE,
    )
    return int(match[1]) if match else None


def manuscript_tex(
    text: str,
    spec: PrintSpec,
    *,
    image_manifest: dict[int, str] | None = None,
    language_code: str = "en",
    composition: dict | None = None,
) -> str:
    if not text.strip() or len(text.encode("utf-8")) > 50 * 1024 * 1024:
        raise ValueError("Manuscrito vazio ou maior que 50 MiB.")
    # Do not silently drop illustrations, tables, HTML, YAML or rich inline markup.
    if re.search(r"!\[|<[/!A-Za-z]|```|^---\s*$|\|.*\||\[[^\]]+\]\(|\*\*|__|^\s*[-*+]\s", text, re.M):
        raise ValueError("Estrutura rica não suportada pelo compositor de texto: requer adaptador editorial de leitura.")
    sections = []
    headings = 0
    image_manifest = image_manifest or {}
    body_started = False
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    frontispiece_lines: list[str] | None = None
    frontispiece_active = False
    normalized_blocks: list[str] = []
    for block in blocks:
        stripped = block.strip()
        if re.fullmatch(r"#\s+Frontispiece", stripped, flags=re.IGNORECASE):
            frontispiece_lines = []
            frontispiece_active = True
            continue
        if frontispiece_active:
            if re.fullmatch(r":::\s*pagebreak\s*\n:::", stripped, flags=re.IGNORECASE):
                normalized_blocks.append("# Frontispiece\n" + "\n".join(frontispiece_lines or []))
                normalized_blocks.append(stripped)
                frontispiece_active = False
            else:
                frontispiece_lines.extend(block.splitlines())
            continue
        normalized_blocks.append(block)
    if frontispiece_active:
        normalized_blocks.append("# Frontispiece\n" + "\n".join(frontispiece_lines or []))

    frontispiece_tokens = {
        re.sub(r"^#+\s*", "", line).strip("*").strip().casefold()
        for line in (frontispiece_lines or [])
        if line.strip()
    }
    first_chapter_seen = False
    frontispiece_seen = False

    for block in normalized_blocks:
        if re.fullmatch(r":::\s*pagebreak\s*\n:::", block.strip(), flags=re.IGNORECASE):
            sections.append("\\clearpage\n")
            continue
        lines = block.splitlines()
        if lines and re.fullmatch(r"#\s+Frontispiece", lines[0].strip(), flags=re.IGNORECASE):
            sections.append(_frontispiece_page(lines[1:]))
            frontispiece_seen = True
            continue
        for line in lines:
            heading = re.fullmatch(r"(#{1,3})\s+(.+)", line)
            if heading:
                headings += 1
                heading_text = heading[2].strip()
                chapter_number = _chapter_number(heading_text)
                if chapter_number is not None:
                    image_path = image_manifest.get(chapter_number)
                    if image_path:
                        sections.append(
                            "\\illustrationpage{" + escape_latex(heading_text) + "}{" + _safe_image_path(image_path) + "}\n"
                        )
                    if not body_started:
                        sections.append("\\mainmatter\n")
                        body_started = True
                    if not image_path:
                        sections.append("\\chapterheading{" + escape_latex(heading_text) + "}\n")
                else:
                    sections.append("\\frontheading{" + escape_latex(heading_text) + "}\n")
            else:
                if frontispiece_seen and not first_chapter_seen:
                    normalized_line = line.strip("*").strip().casefold()
                    if normalized_line in frontispiece_tokens or any(
                        normalized_line.startswith(token + " —") for token in frontispiece_tokens if token
                    ):
                        continue
                sections.append(escape_latex(line) + "\n")
        if any(_chapter_number(re.sub(r"^#{1,3}\s+", "", line.strip())) is not None for line in lines):
            first_chapter_seen = True
        sections.append("\n")
    if not headings:
        raise ValueError("Faltam títulos estruturais Markdown; encaminhe a estrutura ao editorial.")
    end_label = escape_latex(_end_label(language_code))
    composition = composition or {}
    paragraph_indent = str(composition.get("paragraph_indent_em", "1.25"))
    chapter_opening = composition.get("chapter_opening", "recto")
    open_mode = "openright" if chapter_opening == "recto" else "openany"
    chapter_break = "" if chapter_opening == "inline" else r"\clearpage"
    illustration_break = r"\cleardoublepage" if chapter_opening == "recto" else r"\clearpage"
    header_style = composition.get("header_style", "none")
    footer_style = composition.get("footer_style", "page")
    page_numbering = bool(composition.get("page_numbering", True))
    even_header = r"\small\thepage" if header_style == "title" else ""
    odd_header = r"\small\leftmark" if header_style == "chapter" else ""
    footer_number = r"\small\thepage" if footer_style == "page" and page_numbering else ""
    return rf"""\documentclass[twoside,{open_mode}]{{memoir}}
\usepackage{{fontspec}}
\usepackage{{microtype}}
\usepackage{{graphicx}}
\usepackage{{geometry}}
\geometry{{paperwidth={spec.trim_width_in}in,paperheight={spec.trim_height_in}in,inner={spec.inner_margin_in}in,outer={spec.outer_margin_in}in,top={spec.top_margin_in}in,bottom={spec.bottom_margin_in}in}}
\setmainfont{{{escape_latex(spec.body_font)}}}
\setlength{{\parindent}}{{{paragraph_indent}em}}
\setlength{{\parskip}}{{0pt}}
\clubpenalty=10000
\widowpenalty=10000
\setsecnumdepth{{none}}
\raggedbottom
\makepagestyle{{gaiden}}
\makeevenhead{{gaiden}}{{{even_header}}}{{}}{{}}
\makeoddhead{{gaiden}}{{}}{{}}{{{odd_header}}}
\makeevenfoot{{gaiden}}{{}}{{{footer_number}}}{{}}
\makeoddfoot{{gaiden}}{{}}{{{footer_number}}}{{}}
\aliaspagestyle{{chapter}}{{gaiden}}
\pagestyle{{gaiden}}
\newcommand{{\frontispiecepage}}{{\clearpage\thispagestyle{{empty}}\vspace*{{0.20in}}\begin{{center}}\fontsize{{10}}{{12}}\selectfont}}
\newcommand{{\closefrontispiecepage}}{{\end{{center}}\clearpage\thispagestyle{{empty}}}}
\newcommand{{\frontheading}}[1]{{\clearpage\thispagestyle{{gaiden}}\vspace*{{0.30in}}{{\Large\bfseries #1\par}}\vspace{{0.25in}}}}
\newcommand{{\chapterheading}}[1]{{{chapter_break}\thispagestyle{{gaiden}}\vspace*{{0.28in}}\begin{{center}}{{\Large\bfseries #1}}\end{{center}}\vspace{{0.25in}}}}
\newcommand{{\illustrationpage}}[2]{{%
  {illustration_break}\thispagestyle{{empty}}%
  \begin{{center}}\vspace*{{0.12in}}%
  {{\Large\bfseries #1\par}}\vspace{{0.22in}}%
  \includegraphics[width=\textwidth,height=0.68\textheight,keepaspectratio]{{#2}}%
  \end{{center}}\clearpage}}
\newcommand{{\printendmark}}[1]{{%
  \thispagestyle{{empty}}\vspace*{{\fill}}%
  \begin{{center}}{{\Large\itshape #1}}\end{{center}}%
  \vspace*{{\fill}}}}
\begin{{document}}
\fontsize{{{spec.body_font_size_pt}}}{{{spec.body_leading_pt}}}\selectfont
\frontmatter
""" + "".join(sections) + r"""
\clearpage
\ifodd\value{page}\thispagestyle{empty}\null\newpage\fi
\printendmark{""" + end_label + r"""}
\clearpage
\end{document}
"""
