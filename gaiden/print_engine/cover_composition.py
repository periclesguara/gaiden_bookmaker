"""Compose cover artwork around a provider-confirmed spine.

The engine supports two deterministic inputs: supplied PDF panels and a small
text-and-color cover design. Both produce one full-wrap PDF; industrial
preflight remains mandatory downstream.
"""

from __future__ import annotations

import re
from decimal import Decimal

from .geometry import CoverGeometry


def image_print_metrics(
    width_px: int, height_px: int, panel_width_in: Decimal, panel_height_in: Decimal,
    *, fit: str = "cover", scale_percent: int | Decimal = 100,
) -> dict[str, Decimal | int | str]:
    """Return the physical drawing box and effective DPI used by preview/PDF.

    DPI is derived from real pixels and printed dimensions, never from the
    metadata declared inside the image file. Cropping changes visibility but
    does not invent pixels; enlarging the drawing box lowers effective DPI.
    """
    if width_px <= 0 or height_px <= 0 or panel_width_in <= 0 or panel_height_in <= 0:
        raise ValueError("image and panel dimensions must be positive")
    if fit not in {"cover", "contain"}:
        raise ValueError("unsupported cover image fit")
    scale = Decimal(str(scale_percent)) / Decimal("100")
    if not Decimal("0.25") <= scale <= Decimal("4"):
        raise ValueError("image scale must be between 25% and 400%")
    image_aspect = Decimal(width_px) / Decimal(height_px)
    panel_aspect = panel_width_in / panel_height_in
    use_width = (fit == "cover" and image_aspect < panel_aspect) or (
        fit == "contain" and image_aspect >= panel_aspect
    )
    if use_width:
        printed_width = panel_width_in * scale
        printed_height = printed_width / image_aspect
    else:
        printed_height = panel_height_in * scale
        printed_width = printed_height * image_aspect
    return {
        "fit": fit,
        "scale_percent": int(Decimal(str(scale_percent))),
        "printed_width_in": printed_width,
        "printed_height_in": printed_height,
        "effective_dpi_x": Decimal(width_px) / printed_width,
        "effective_dpi_y": Decimal(height_px) / printed_height,
    }


def _image_panel_block(
    *, panel_x: Decimal, panel_width: Decimal, panel_height: Decimal, image_name: str,
    width_px: int, height_px: int, fit: str, scale_percent: int,
    focal_x: int, focal_y: int, offset_x_mm: Decimal, offset_y_mm: Decimal,
    rotation_deg: Decimal, overlay_opacity: int = 0,
) -> str:
    metrics = image_print_metrics(width_px, height_px, panel_width, panel_height, fit=fit, scale_percent=scale_percent)
    draw_width = metrics["printed_width_in"]
    draw_height = metrics["printed_height_in"]
    overflow_x = max(Decimal("0"), draw_width - panel_width)
    overflow_y = max(Decimal("0"), draw_height - panel_height)
    center_x = panel_width / 2 - (Decimal(focal_x) - 50) / 100 * overflow_x + offset_x_mm / Decimal("25.4")
    center_y = panel_height / 2 + (Decimal(focal_y) - 50) / 100 * overflow_y - offset_y_mm / Decimal("25.4")
    overlay = Decimal(str(overlay_opacity)) / Decimal("100")
    overlay_line = rf"\fill[coverbg,opacity={overlay}] (0,0) rectangle ({panel_width},{panel_height});" if overlay else ""
    return rf"""\begin{{textblock*}}{{{panel_width}in}}({panel_x}in,0in)
\begin{{tikzpicture}}[x=1in,y=1in]
\clip (0,0) rectangle ({panel_width},{panel_height});
\node[inner sep=0pt,rotate={rotation_deg}] at ({center_x},{center_y}) {{\includegraphics[width={draw_width}in,height={draw_height}in]{{{image_name}}}}};
{overlay_line}
\end{{tikzpicture}}
\end{{textblock*}}
"""


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


def _font_decorations(weight: str, style: str, letter_spacing: Decimal | int | str) -> str:
    if weight not in {"regular", "bold"} or style not in {"normal", "italic", "small_caps"}:
        raise ValueError("unsupported cover font styling")
    weight_command = r"\bfseries" if weight == "bold" else ""
    style_command = {"normal": "", "italic": r"\itshape", "small_caps": r"\scshape"}[style]
    return rf"{weight_command}{style_command}\addfontfeatures{{LetterSpace={letter_spacing}}}"


def design_cover_tex(
    geometry: CoverGeometry,
    *,
    title: str,
    author: str,
    subtitle: str = "",
    language: str = "",
    publisher: str = "",
    front_title_x_percent: int = 50,
    front_title_y_percent: int = 20,
    front_subtitle_x_percent: int = 50,
    front_subtitle_y_percent: int = 34,
    front_author_x_percent: int = 50,
    front_author_y_percent: int = 84,
    front_language_x_percent: int = 50,
    front_language_y_percent: int = 84,
    front_text_scale_percent: int = 100,
    front_text_align: str = "center",
    front_publisher_y_percent: int = 89,
    front_publisher_x_percent: int = 50,
    cover_font_family: str = "TeX Gyre Pagella",
    front_title_font_family: str = "",
    front_subtitle_font_family: str = "",
    front_author_font_family: str = "",
    front_language_font_family: str = "",
    front_publisher_font_family: str = "",
    front_title_color: str = "",
    front_subtitle_color: str = "",
    front_author_color: str = "",
    front_language_color: str = "",
    front_publisher_color: str = "",
    front_title_font_size_pt: Decimal | int | str = 28,
    front_subtitle_font_size_pt: Decimal | int | str = 14,
    front_author_font_size_pt: Decimal | int | str = 12,
    front_language_font_size_pt: Decimal | int | str = 8,
    front_publisher_font_size_pt: Decimal | int | str = 10,
    front_title_weight: str = "bold",
    front_title_style: str = "normal",
    front_subtitle_weight: str = "regular",
    front_subtitle_style: str = "normal",
    front_author_weight: str = "regular",
    front_author_style: str = "normal",
    front_language_weight: str = "regular",
    front_language_style: str = "normal",
    front_publisher_weight: str = "regular",
    front_publisher_style: str = "normal",
    front_title_letter_spacing_pt: Decimal | int | str = 0,
    front_subtitle_letter_spacing_pt: Decimal | int | str = 0,
    front_author_letter_spacing_pt: Decimal | int | str = 0,
    front_language_letter_spacing_pt: Decimal | int | str = 0,
    front_publisher_letter_spacing_pt: Decimal | int | str = 0,
    back_text: str = "",
    back_heading: str = "",
    back_quote: str = "",
    back_bio: str = "",
    back_footer: str = "",
    back_heading_font_size_pt: Decimal | int | str = 17,
    back_body_font_size_pt: Decimal | int | str = 10.5,
    back_text_align: str = "left",
    back_text_x_percent: int = 50,
    back_text_y_percent: int = 12,
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
    image_offset_x_mm: Decimal | int | str = 0,
    image_offset_y_mm: Decimal | int | str = 0,
    image_rotation_deg: Decimal | int | str = 0,
    back_image_name: str | None = None,
    back_image_width_px: int | None = None,
    back_image_height_px: int | None = None,
    back_image_fit: str = "cover",
    back_image_zoom: int = 100,
    back_image_x: int = 50,
    back_image_y: int = 50,
    back_image_offset_x_mm: Decimal | int | str = 0,
    back_image_offset_y_mm: Decimal | int | str = 0,
    back_image_rotation_deg: Decimal | int | str = 0,
    spine_image_name: str | None = None,
    spine_image_width_px: int | None = None,
    spine_image_height_px: int | None = None,
    spine_image_fit: str = "cover",
    spine_image_zoom: int = 100,
    spine_image_x: int = 50,
    spine_image_y: int = 50,
    spine_title: str | None = None,
    spine_author: str = "",
    spine_publisher: str = "",
    spine_font_family: str = "TeX Gyre Pagella",
    spine_font_size_pt: Decimal | int | str = 8,
    spine_font_weight: str = "regular",
    spine_color: str = "#FFFFFF",
    spine_letter_spacing_pt: Decimal | int | str = 0,
    spine_align_horizontal: str = "center",
    spine_align_vertical: str = "center",
    spine_offset_x_mm: Decimal | int | str = 0,
    spine_offset_y_mm: Decimal | int | str = 0,
    spine_text_x_percent: int = 50,
    spine_text_y_percent: int = 50,
    spine_rotation: int | str = -90,
    spine_edge_safe_mm: Decimal | int | str = 1.6,
    overlay_opacity: int = 25,
    reserve_barcode: bool = True,
    back_elements: dict | None = None,
) -> str:
    """Render a self-contained, text-first cover-builder layout.

    Coordinates are derived from the finalized provider geometry. The optional
    image is a front-panel background and is always referenced by a fixed
    filename chosen by the caller, never by an operator-supplied path.
    """
    if layout not in {"classic", "modern", "minimal", "art_only"}:
        raise ValueError("unsupported cover layout")
    if image_fit not in {"cover", "contain"} or back_image_fit not in {"cover", "contain"} or spine_image_fit not in {"cover", "contain"}:
        raise ValueError("unsupported cover image fit")
    if not 25 <= int(image_zoom) <= 400 or not 0 <= int(image_x) <= 100 or not 0 <= int(image_y) <= 100:
        raise ValueError("invalid cover image framing")
    if not 25 <= int(back_image_zoom) <= 400 or not 0 <= int(back_image_x) <= 100 or not 0 <= int(back_image_y) <= 100:
        raise ValueError("invalid back-cover image framing")
    if not 25 <= int(spine_image_zoom) <= 400 or not 0 <= int(spine_image_x) <= 100 or not 0 <= int(spine_image_y) <= 100:
        raise ValueError("invalid spine image framing")
    if not 0 <= int(overlay_opacity) <= 80:
        raise ValueError("invalid cover overlay opacity")
    if not 8 <= int(front_title_y_percent) <= 70 or not 45 <= int(front_author_y_percent) <= 92:
        raise ValueError("invalid front-cover text position")
    for position in (front_title_x_percent, front_subtitle_x_percent, front_author_x_percent, front_language_x_percent, front_publisher_x_percent):
        if not 10 <= int(position) <= 90:
            raise ValueError("invalid front-cover horizontal position")
    if not 10 <= int(front_subtitle_y_percent) <= 82 or not 78 <= int(front_publisher_y_percent) <= 96:
        raise ValueError("invalid front-cover secondary text position")
    if not 70 <= int(front_language_y_percent) <= 94:
        raise ValueError("invalid front-cover language position")
    if not 10 <= int(back_text_x_percent) <= 90 or not 8 <= int(back_text_y_percent) <= 75:
        raise ValueError("invalid back-cover text position")
    if not 10 <= int(spine_text_x_percent) <= 90 or not 10 <= int(spine_text_y_percent) <= 90:
        raise ValueError("invalid spine text position")
    if not 50 <= int(front_text_scale_percent) <= 160:
        raise ValueError("invalid front-cover text scale")
    if front_text_align not in {"left", "center", "right"}:
        raise ValueError("unsupported front-cover text alignment")
    if back_text_align not in {"left", "justify", "center"}:
        raise ValueError("unsupported back-cover text alignment")
    title_decorations = _font_decorations(front_title_weight, front_title_style, front_title_letter_spacing_pt)
    subtitle_decorations = _font_decorations(front_subtitle_weight, front_subtitle_style, front_subtitle_letter_spacing_pt)
    author_decorations = _font_decorations(front_author_weight, front_author_style, front_author_letter_spacing_pt)
    language_decorations = _font_decorations(front_language_weight, front_language_style, front_language_letter_spacing_pt)
    publisher_decorations = _font_decorations(front_publisher_weight, front_publisher_style, front_publisher_letter_spacing_pt)
    supported_fonts = {
        "EB Garamond", "GFS Didot", "GFS BodoniClassic", "GFS Baskerville",
        "Berenis ADF Pro", "Accanthis ADF Std", "Gentium Book Plus",
        "Charis SIL", "Noto Serif Display", "Fira Sans",
        # Accepted only for backward-compatible rendering of saved designs.
        "TeX Gyre Pagella", "TeX Gyre Termes", "TeX Gyre Schola",
        "TeX Gyre Bonum", "Libertinus Serif", "Noto Serif",
    }
    if cover_font_family not in supported_fonts:
        raise ValueError("unsupported cover font")
    front_title_font_family = front_title_font_family or cover_font_family
    front_subtitle_font_family = front_subtitle_font_family or cover_font_family
    front_author_font_family = front_author_font_family or cover_font_family
    front_language_font_family = front_language_font_family or cover_font_family
    front_publisher_font_family = front_publisher_font_family or cover_font_family
    if any(font not in supported_fonts for font in (
        front_title_font_family, front_subtitle_font_family,
        front_author_font_family, front_language_font_family, front_publisher_font_family,
    )):
        raise ValueError("unsupported front-cover font")
    if spine_font_family not in supported_fonts:
        raise ValueError("unsupported spine font")
    if spine_font_weight not in {"regular", "bold"}:
        raise ValueError("unsupported spine font weight")
    if spine_align_horizontal not in {"left", "center", "right"} or spine_align_vertical not in {"top", "center", "bottom"}:
        raise ValueError("unsupported spine alignment")
    if int(spine_rotation) not in {-90, 90}:
        raise ValueError("unsupported spine rotation")
    if layout != "art_only" and (not title.strip() or not author.strip()):
        raise ValueError("cover title and author are required")
    background = _hex_color(background_color)
    text = _hex_color(text_color)
    accent = _hex_color(accent_color)
    title_color_value = _hex_color(front_title_color or text_color)
    subtitle_color_value = _hex_color(front_subtitle_color or accent_color)
    author_color_value = _hex_color(front_author_color or accent_color)
    language_color_value = _hex_color(front_language_color or accent_color)
    publisher_color_value = _hex_color(front_publisher_color or accent_color)
    back_background = _hex_color(back_background_color)
    back_text_color = _hex_color(back_text_color)
    back_accent = _hex_color(back_accent_color)
    spine_color_value = _hex_color(spine_color)
    panel_width = geometry.trim_width_in + geometry.bleed_in
    front_x = panel_width + geometry.spine_width_in
    text_width = geometry.trim_width_in - Decimal("0.70")
    def text_x(panel_origin: Decimal, percent: int) -> Decimal:
        center = geometry.bleed_in + geometry.trim_width_in * Decimal(str(percent)) / Decimal("100")
        return panel_origin + center - text_width / 2
    text_scale = Decimal(str(front_text_scale_percent)) / Decimal("100")
    title_size = Decimal(str(front_title_font_size_pt)) * text_scale
    subtitle_size = Decimal(str(front_subtitle_font_size_pt)) * text_scale
    author_size = Decimal(str(front_author_font_size_pt)) * text_scale
    language_size = Decimal(str(front_language_font_size_pt)) * text_scale
    publisher_size = Decimal(str(front_publisher_font_size_pt)) * text_scale
    back_heading_size = Decimal(str(back_heading_font_size_pt))
    back_body_size = Decimal(str(back_body_font_size_pt))
    title_y = geometry.bleed_in + geometry.trim_height_in * Decimal(str(front_title_y_percent)) / Decimal("100")
    subtitle_y = geometry.bleed_in + geometry.trim_height_in * Decimal(str(front_subtitle_y_percent)) / Decimal("100")
    author_y = geometry.bleed_in + geometry.trim_height_in * Decimal(str(front_author_y_percent)) / Decimal("100")
    language_y = geometry.bleed_in + geometry.trim_height_in * Decimal(str(front_language_y_percent)) / Decimal("100")
    publisher_y = geometry.bleed_in + geometry.trim_height_in * Decimal(str(front_publisher_y_percent)) / Decimal("100")
    front_alignment = {"left": r"\raggedright", "center": r"\centering", "right": r"\raggedleft"}[front_text_align]
    image_block = ""
    if image_name:
        image_block = _image_panel_block(
            panel_x=front_x, panel_width=panel_width, panel_height=geometry.spread_height_in,
            image_name=image_name, width_px=int(image_width_px or 1), height_px=int(image_height_px or 1),
            fit=image_fit, scale_percent=int(image_zoom), focal_x=int(image_x), focal_y=int(image_y),
            offset_x_mm=Decimal(str(image_offset_x_mm)), offset_y_mm=Decimal(str(image_offset_y_mm)),
            rotation_deg=Decimal(str(image_rotation_deg)), overlay_opacity=overlay_opacity,
        )
    back_image_block = ""
    if back_image_name:
        back_image_block = _image_panel_block(
            panel_x=Decimal("0"), panel_width=panel_width, panel_height=geometry.spread_height_in,
            image_name=back_image_name, width_px=int(back_image_width_px or 1), height_px=int(back_image_height_px or 1),
            fit=back_image_fit, scale_percent=int(back_image_zoom), focal_x=int(back_image_x), focal_y=int(back_image_y),
            offset_x_mm=Decimal(str(back_image_offset_x_mm)), offset_y_mm=Decimal(str(back_image_offset_y_mm)),
            rotation_deg=Decimal(str(back_image_rotation_deg)),
        )
    spine_image_block = ""
    if spine_image_name and geometry.spine_width_in > 0:
        spine_image_block = _image_panel_block(
            panel_x=panel_width, panel_width=geometry.spine_width_in, panel_height=geometry.spread_height_in,
            image_name=spine_image_name, width_px=int(spine_image_width_px or 1), height_px=int(spine_image_height_px or 1),
            fit=spine_image_fit, scale_percent=int(spine_image_zoom), focal_x=int(spine_image_x), focal_y=int(spine_image_y),
            offset_x_mm=Decimal("0"), offset_y_mm=Decimal("0"), rotation_deg=Decimal("0"),
        )
    spine_block = ""
    effective_spine_title = spine_text if spine_title is None else spine_title
    spine_copy = " · ".join(part.strip() for part in (effective_spine_title, spine_author, spine_publisher) if part.strip())
    if spine_copy and geometry.spine_text_allowed:
        font_weight = r"\bfseries" if spine_font_weight == "bold" else ""
        edge = Decimal(str(spine_edge_safe_mm)) / Decimal("25.4")
        spine_x_local = geometry.spine_width_in * Decimal(str(spine_text_x_percent)) / Decimal("100")
        spine_y = geometry.spread_height_in * Decimal(str(spine_text_y_percent)) / Decimal("100")
        spine_x_local += Decimal(str(spine_offset_x_mm)) / Decimal("25.4")
        spine_y += Decimal(str(spine_offset_y_mm)) / Decimal("25.4")
        # Editor coordinates start at the top; TikZ's Y axis starts at the bottom.
        spine_y = geometry.spread_height_in - spine_y
        spine_block = rf"""\begin{{textblock*}}{{{geometry.spine_width_in}in}}({panel_width}in,0in)
\begin{{tikzpicture}}[x=1in,y=1in]
\clip (0,0) rectangle ({geometry.spine_width_in},{geometry.spread_height_in});
\node[inner sep=0pt,rotate={int(spine_rotation)},anchor=center] at ({spine_x_local},{spine_y}) {{\spinefont\color{{spinecolor}}{font_weight}\fontsize{{{spine_font_size_pt}}}{{{Decimal(str(spine_font_size_pt)) * Decimal('1.2')}}}\selectfont\addfontfeatures{{LetterSpace={spine_letter_spacing_pt}}}{_tex_text(spine_copy)}}};
\end{{tikzpicture}}
\end{{textblock*}}
"""
    back_blocks = []
    if back_heading.strip():
        back_blocks.append(rf"{{\color{{backaccent}}\bfseries\fontsize{{{back_heading_size}}}{{{back_heading_size * Decimal('1.25')}}}\selectfont {_tex_text(back_heading)}\par\vspace{{0.18in}}}}")
    if back_quote.strip():
        back_blocks.append(rf"{{\color{{backaccent}}\itshape\fontsize{{11}}{{15}}\selectfont ``{_tex_paragraph(back_quote)}''\par\vspace{{0.18in}}}}")
    if back_text.strip():
        back_blocks.append(rf"{{\color{{backtext}}\fontsize{{{back_body_size}}}{{{back_body_size * Decimal('1.4')}}}\selectfont {_tex_paragraph(back_text)}\par\vspace{{0.20in}}}}")
    if back_bio.strip():
        back_blocks.append(rf"{{\color{{backtext}}\fontsize{{8.5}}{{12}}\selectfont \textbf{{Sobre o autor}}\par {_tex_paragraph(back_bio)}\par\vspace{{0.16in}}}}")
    if back_footer.strip():
        back_blocks.append(rf"{{\color{{backtext}}\fontsize{{7.5}}{{10}}\selectfont {_tex_paragraph(back_footer)}\par}}")
    back_block = ""
    if back_blocks:
        back_height = geometry.spread_height_in - Decimal("2.20")
        back_alignment = {"left": r"\raggedright", "justify": r"\justifying", "center": r"\centering"}[back_text_align]
        back_text_x = text_x(Decimal("0"), int(back_text_x_percent))
        back_text_y = geometry.bleed_in + geometry.trim_height_in * Decimal(str(back_text_y_percent)) / Decimal("100")
        back_block = rf"""\begin{{textblock*}}{{{text_width}in}}({back_text_x}in,{back_text_y}in)
\begin{{adjustbox}}{{max width={text_width}in,max height={back_height}in}}
\begin{{minipage}}{{{text_width}in}}{back_alignment}
{''.join(back_blocks)}
\end{{minipage}}
\end{{adjustbox}}
\end{{textblock*}}"""
    # Older designs keep their original flowing back block. New designs carry
    # independently positioned fields, each with validated production styling.
    if back_elements and any(back_elements.values()):
        independent = []
        copies = {"heading": back_heading, "quote": back_quote, "body": back_text,
                  "bio": back_bio, "footer": back_footer}
        defaults = {"heading": (17, 12), "quote": (11, 25), "body": (10.5, 38),
                    "bio": (8.5, 65), "footer": (7.5, 82)}
        for key, copy in copies.items():
            if not copy.strip():
                continue
            prefix = f"back_element_{key}"
            get = lambda name, default: back_elements.get(f"{prefix}_{name}") or default
            family = get("font_family", cover_font_family)
            if family not in supported_fonts:
                raise ValueError("unsupported back-cover font")
            size = Decimal(str(get("font_size_pt", defaults[key][0])))
            spacing = Decimal(str(get("letter_spacing_pt", 0)))
            x, y = int(get("x_percent", 50)), int(get("y_percent", defaults[key][1]))
            if not 6 <= size <= 72 or not -1 <= spacing <= 8 or not 10 <= x <= 90 or not 8 <= y <= 90:
                raise ValueError("invalid back-cover element typography or position")
            decorations = _font_decorations(get("weight", "regular"), get("style", "normal"), spacing)
            color = _hex_color(get("color", back_accent_color if key in {"heading", "quote"} else back_text_color))
            xpos = text_x(Decimal("0"), x)
            ypos = geometry.bleed_in + geometry.trim_height_in * Decimal(y) / Decimal("100")
            content = _tex_text(copy) if key == "heading" else _tex_paragraph(copy)
            independent.append(rf"""\begin{{textblock*}}{{{text_width}in}}({xpos}in,{ypos}in)
\begin{{minipage}}{{{text_width}in}}{back_alignment}
\fontspec{{{family}}}\color[HTML]{{{color}}}{decorations}\fontsize{{{size}}}{{{size * Decimal('1.4')}}}\selectfont {content}
\end{{minipage}}
\end{{textblock*}}""")
        back_block = "\n".join(independent)
    front_copy_block = ""
    if layout != "art_only":
        # Language and publisher are independent editorial fields so each one
        # keeps its own position, font and color on the front cover.
        publisher_copy = publisher.strip()
        publisher_block = (
            rf"""\begin{{textblock*}}{{{text_width}in}}({text_x(front_x, int(front_publisher_x_percent))}in,{publisher_y}in)
{front_alignment}\publisherfont\color{{publishercolor}}{publisher_decorations}\fontsize{{{publisher_size}}}{{{publisher_size + Decimal('3')}}}\selectfont {_tex_text(publisher_copy)}
\end{{textblock*}}"""
            if publisher_copy else ""
        )
        language_copy = language.strip()
        language_block = (
            rf"""\begin{{textblock*}}{{{text_width}in}}({text_x(front_x, int(front_language_x_percent))}in,{language_y}in)
{front_alignment}\languagefont\color{{languagecolor}}{language_decorations}\fontsize{{{language_size}}}{{{language_size + Decimal('3')}}}\selectfont {_tex_text(language_copy)}
\end{{textblock*}}"""
            if language_copy else ""
        )
        subtitle_block = (
            rf"""\begin{{textblock*}}{{{text_width}in}}({text_x(front_x, int(front_subtitle_x_percent))}in,{subtitle_y}in)
{front_alignment}\subtitlefont\color{{subtitlecolor}}{subtitle_decorations}\fontsize{{{subtitle_size}}}{{{subtitle_size + Decimal('4')}}}\selectfont {_tex_text(subtitle)}
\end{{textblock*}}"""
            if subtitle.strip() else ""
        )
        front_copy_block = rf"""\begin{{textblock*}}{{{text_width}in}}({text_x(front_x, int(front_title_x_percent))}in,{title_y}in)
{front_alignment}\titlefont\color{{titlecolor}}{title_decorations}\fontsize{{{title_size}}}{{{title_size + Decimal('7')}}}\selectfont {_tex_text(title)}\par
\end{{textblock*}}
{subtitle_block}
\begin{{textblock*}}{{{text_width}in}}({text_x(front_x, int(front_author_x_percent))}in,{author_y}in)
{front_alignment}\authorfont\color{{authorcolor}}{author_decorations}\fontsize{{{author_size}}}{{{author_size + Decimal('4')}}}\selectfont {_tex_text(author)}
\end{{textblock*}}
{language_block}
{publisher_block}
"""
    barcode_block = ""
    if reserve_barcode:
        # The provider barcode must remain inside the trimmed back-cover panel,
        # with 1.10 in of safety below it.  This leaves room for the provider
        # code, the trim tolerance, and a visible separation from cover art.
        # Using spread_height_in placed its
        # lower edge in the bleed area whenever the provider required bleed.
        barcode_y = geometry.bleed_in + geometry.trim_height_in - Decimal("2.15")
        barcode_block = rf"""\begin{{textblock*}}{{1.75in}}({geometry.bleed_in + geometry.trim_width_in - Decimal("2.05")}in,{barcode_y}in)
\colorbox{{white}}{{\rule{{0pt}}{{1.05in}}\rule{{1.67in}}{{0pt}}}}
\end{{textblock*}}
"""
    # Inactive fields must not make a proof depend on their configured fonts.
    families = (
        ("spine", spine_font_family, spine_block),
        ("title", front_title_font_family, front_copy_block),
        ("subtitle", front_subtitle_font_family, front_copy_block and subtitle.strip()),
        ("author", front_author_font_family, front_copy_block),
        ("language", front_language_font_family, front_copy_block and language.strip()),
        ("publisher", front_publisher_font_family, front_copy_block and publisher.strip()),
    )
    font_declarations = "\n".join(
        rf"\newfontfamily\{name}font{{{family}}}"
        for name, family, used in families if used
    )
    main_font = rf"\setmainfont{{{cover_font_family}}}" if front_copy_block or back_block else ""
    return rf"""\documentclass{{article}}
\usepackage[paperwidth={geometry.spread_width_in}in,paperheight={geometry.spread_height_in}in,margin=0in]{{geometry}}
\usepackage{{graphicx}}
\usepackage{{fontspec}}
\usepackage{{xcolor}}
\usepackage{{textpos}}
\usepackage{{tikz}}
\usepackage{{adjustbox}}
\usepackage{{ragged2e}}
\pagestyle{{empty}}
\definecolor{{coverbg}}{{HTML}}{{{background}}}
\definecolor{{covertext}}{{HTML}}{{{text}}}
\definecolor{{coveraccent}}{{HTML}}{{{accent}}}
\definecolor{{titlecolor}}{{HTML}}{{{title_color_value}}}
\definecolor{{subtitlecolor}}{{HTML}}{{{subtitle_color_value}}}
\definecolor{{authorcolor}}{{HTML}}{{{author_color_value}}}
\definecolor{{languagecolor}}{{HTML}}{{{language_color_value}}}
\definecolor{{publishercolor}}{{HTML}}{{{publisher_color_value}}}
\definecolor{{backbg}}{{HTML}}{{{back_background}}}
\definecolor{{backtext}}{{HTML}}{{{back_text_color}}}
\definecolor{{backaccent}}{{HTML}}{{{back_accent}}}
\definecolor{{spinecolor}}{{HTML}}{{{spine_color_value}}}
{font_declarations}
{main_font}
\pagecolor{{coverbg}}
\setlength{{\TPHorizModule}}{{1in}}
\setlength{{\TPVertModule}}{{1in}}
\begin{{document}}
\null
\begin{{textblock*}}{{{panel_width}in}}(0in,0in)\colorbox{{backbg}}{{\rule{{0pt}}{{{geometry.spread_height_in}in}}\rule{{{panel_width}in}}{{0pt}}}}\end{{textblock*}}
{back_image_block}
{image_block}
{spine_image_block}
{front_copy_block}
{back_block}
{barcode_block}
{spine_block}
\end{{document}}
"""
