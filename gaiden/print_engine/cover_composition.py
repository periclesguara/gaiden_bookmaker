"""Compose cover artwork around a provider-confirmed spine.

The engine supports two deterministic inputs: supplied PDF panels and a small
text-and-color cover design. Both produce one full-wrap PDF; industrial
preflight remains mandatory downstream.
"""

from __future__ import annotations

import re
from decimal import Decimal

from .geometry import CoverGeometry


def cover_tex(geometry: CoverGeometry) -> str:
    panel_width = geometry.trim_width_in + geometry.bleed_in
    front_x = panel_width + geometry.spine_width_in
    return rf"""\documentclass{{article}}
\usepackage[paperwidth={geometry.spread_width_in}in,paperheight={geometry.spread_height_in}in,margin=0in]{{geometry}}
\usepackage{{graphicx}}
\usepackage[absolute,overlay]{{textpos}}
\pagestyle{{empty}}
\setlength{{\TPHorizModule}}{{1in}}
\setlength{{\TPVertModule}}{{1in}}
\begin{{document}}
\null
\begin{{textblock*}}{{{panel_width}in}}(0in,0in)
\noindent\includegraphics[width={panel_width}in,height={geometry.spread_height_in}in,keepaspectratio]{{BACK.pdf}}
\end{{textblock*}}
\begin{{textblock*}}{{{panel_width}in}}({front_x}in,0in)
\noindent\includegraphics[width={panel_width}in,height={geometry.spread_height_in}in,keepaspectratio]{{FRONT.pdf}}
\end{{textblock*}}
\end{{document}}
"""


def _tex_text(value: str) -> str:
    """Escape operator text before placing it in a generated TeX file."""
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def _tex_paragraph(value: str) -> str:
    return r"\par ".join(_tex_text(line.strip()) for line in value.splitlines() if line.strip())


def _hex_color(value: str) -> str:
    normalized = value.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", normalized):
        raise ValueError("cover colors must be six-digit hexadecimal values")
    return normalized.upper()


def design_cover_tex(
    geometry: CoverGeometry,
    *,
    title: str,
    author: str,
    subtitle: str = "",
    back_text: str = "",
    back_heading: str = "",
    back_quote: str = "",
    back_bio: str = "",
    spine_text: str = "",
    background_color: str = "#1F2937",
    text_color: str = "#FFFFFF",
    accent_color: str = "#D4A24C",
    back_background_color: str = "#1F2937",
    back_text_color: str = "#FFFFFF",
    back_accent_color: str = "#D4A24C",
    layout: str = "classic",
    image_name: str | None = None,
    image_width_px: int | None = None,
    image_height_px: int | None = None,
    image_fit: str = "cover",
    image_zoom: int = 100,
    image_x: int = 50,
    image_y: int = 50,
    overlay_opacity: int = 25,
    reserve_barcode: bool = True,
) -> str:
    """Render a self-contained, text-first cover-builder layout.

    Coordinates are derived from the finalized provider geometry. The optional
    image is a front-panel background and is always referenced by a fixed
    filename chosen by the caller, never by an operator-supplied path.
    """
    if layout not in {"classic", "modern", "minimal", "art_only"}:
        raise ValueError("unsupported cover layout")
    if image_fit not in {"cover", "contain"}:
        raise ValueError("unsupported cover image fit")
    if not 100 <= int(image_zoom) <= 180 or not 0 <= int(image_x) <= 100 or not 0 <= int(image_y) <= 100:
        raise ValueError("invalid cover image framing")
    if not 0 <= int(overlay_opacity) <= 80:
        raise ValueError("invalid cover overlay opacity")
    if layout != "art_only" and (not title.strip() or not author.strip()):
        raise ValueError("cover title and author are required")
    background = _hex_color(background_color)
    text = _hex_color(text_color)
    accent = _hex_color(accent_color)
    back_background = _hex_color(back_background_color)
    back_text_color = _hex_color(back_text_color)
    back_accent = _hex_color(back_accent_color)
    panel_width = geometry.trim_width_in + geometry.bleed_in
    front_x = panel_width + geometry.spine_width_in
    front_text_x = front_x + geometry.bleed_in + Decimal("0.35")
    back_text_x = geometry.bleed_in + Decimal("0.35")
    text_width = geometry.trim_width_in - Decimal("0.70")
    if layout == "modern":
        title_size, author_size, title_y = "30", "13", "1.25"
    elif layout == "minimal":
        title_size, author_size, title_y = "25", "11", "2.00"
    else:
        title_size, author_size, title_y = "28", "12", "1.55"
    image_block = ""
    if image_name:
        zoom = Decimal(str(image_zoom)) / Decimal("100")
        center_x = panel_width / 2 + (Decimal("50") - Decimal(str(image_x))) / Decimal("400") * panel_width
        center_y = geometry.spread_height_in / 2 + (Decimal(str(image_y)) - Decimal("50")) / Decimal("400") * geometry.spread_height_in
        if image_fit == "contain" or not image_width_px or not image_height_px:
            image_options = (
                f"width={panel_width * zoom}in,height={geometry.spread_height_in * zoom}in,keepaspectratio"
            )
        else:
            image_aspect = Decimal(image_width_px) / Decimal(image_height_px)
            panel_aspect = panel_width / geometry.spread_height_in
            if image_aspect >= panel_aspect:
                image_options = f"height={geometry.spread_height_in * zoom}in"
            else:
                image_options = f"width={panel_width * zoom}in"
        overlay = Decimal(str(overlay_opacity)) / Decimal("100")
        image_block = rf"""\begin{{textblock*}}{{{panel_width}in}}({front_x}in,0in)
\begin{{tikzpicture}}[x=1in,y=1in]
\clip (0,0) rectangle ({panel_width},{geometry.spread_height_in});
\node[inner sep=0pt] at ({center_x},{center_y}) {{\includegraphics[{image_options}]{{{image_name}}}}};
\fill[coverbg,opacity={overlay}] (0,0) rectangle ({panel_width},{geometry.spread_height_in});
\end{{tikzpicture}}
\end{{textblock*}}
"""
    spine_block = ""
    if spine_text.strip() and geometry.spine_text_allowed:
        spine_block = rf"""\begin{{textblock*}}{{{geometry.trim_height_in}in}}({panel_width}in,0.25in)
\centering\rotatebox{{90}}{{\color{{covertext}}\fontsize{{10}}{{12}}\selectfont {_tex_text(spine_text)}}}
\end{{textblock*}}
"""
    back_blocks = []
    if back_heading.strip():
        back_blocks.append(rf"""\begin{{textblock*}}{{{text_width}in}}({back_text_x}in,0.75in)
\color{{backaccent}}\raggedright\bfseries\fontsize{{17}}{{21}}\selectfont {_tex_text(back_heading)}
\end{{textblock*}}""")
    if back_quote.strip():
        back_blocks.append(rf"""\begin{{textblock*}}{{{text_width}in}}({back_text_x}in,1.35in)
\color{{backaccent}}\raggedright\itshape\fontsize{{11}}{{15}}\selectfont ``{_tex_paragraph(back_quote)}''
\end{{textblock*}}""")
    if back_text.strip():
        back_blocks.append(rf"""\begin{{textblock*}}{{{text_width}in}}({back_text_x}in,2.15in)
\color{{backtext}}\raggedright\fontsize{{10.5}}{{15}}\selectfont {_tex_paragraph(back_text)}
\end{{textblock*}}""")
    if back_bio.strip():
        back_blocks.append(rf"""\begin{{textblock*}}{{{text_width}in}}({back_text_x}in,{geometry.spread_height_in - Decimal("2.10")}in)
\color{{backtext}}\raggedright\fontsize{{8.5}}{{12}}\selectfont \textbf{{Sobre o autor}}\par {_tex_paragraph(back_bio)}
\end{{textblock*}}""")
    back_block = "\n".join(back_blocks)
    subtitle_block = (
        rf"\vspace{{0.20in}}{{\color{{coveraccent}}\fontsize{{14}}{{18}}\selectfont {_tex_text(subtitle)}\par}}"
        if subtitle.strip()
        else ""
    )
    front_copy_block = ""
    if layout != "art_only":
        front_copy_block = rf"""\begin{{textblock*}}{{{text_width}in}}({front_text_x}in,{title_y}in)
\centering\color{{covertext}}\bfseries\fontsize{{{title_size}}}{{{int(title_size) + 7}}}\selectfont {_tex_text(title)}\par
{subtitle_block}
\end{{textblock*}}
\begin{{textblock*}}{{{text_width}in}}({front_text_x}in,{geometry.spread_height_in - Decimal("0.85")}in)
\centering\color{{coveraccent}}\fontsize{{{author_size}}}{{16}}\selectfont {_tex_text(author)}
\end{{textblock*}}
"""
    barcode_block = ""
    if reserve_barcode:
        barcode_block = rf"""\begin{{textblock*}}{{1.75in}}({geometry.bleed_in + geometry.trim_width_in - Decimal("2.05")}in,{geometry.spread_height_in - Decimal("1.35")}in)
\colorbox{{white}}{{\rule{{0pt}}{{1.05in}}\rule{{1.67in}}{{0pt}}}}
\end{{textblock*}}
"""
    return rf"""\documentclass{{article}}
\usepackage[paperwidth={geometry.spread_width_in}in,paperheight={geometry.spread_height_in}in,margin=0in]{{geometry}}
\usepackage{{graphicx}}
\usepackage{{xcolor}}
\usepackage{{textpos}}
\usepackage{{tikz}}
\pagestyle{{empty}}
\definecolor{{coverbg}}{{HTML}}{{{background}}}
\definecolor{{covertext}}{{HTML}}{{{text}}}
\definecolor{{coveraccent}}{{HTML}}{{{accent}}}
\definecolor{{backbg}}{{HTML}}{{{back_background}}}
\definecolor{{backtext}}{{HTML}}{{{back_text_color}}}
\definecolor{{backaccent}}{{HTML}}{{{back_accent}}}
\pagecolor{{coverbg}}
\setlength{{\TPHorizModule}}{{1in}}
\setlength{{\TPVertModule}}{{1in}}
\begin{{document}}
\null
\begin{{textblock*}}{{{panel_width}in}}(0in,0in)\colorbox{{backbg}}{{\rule{{0pt}}{{{geometry.spread_height_in}in}}\rule{{{panel_width}in}}{{0pt}}}}\end{{textblock*}}
{image_block}
{front_copy_block}
{back_block}
{barcode_block}
{spine_block}
\end{{document}}
"""
