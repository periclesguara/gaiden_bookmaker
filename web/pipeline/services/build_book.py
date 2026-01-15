from __future__ import annotations

from django.template.loader import render_to_string

from . import paths


def run_build(edition) -> dict:
    final_md = paths.final_md_path(edition)
    if not final_md.exists():
        raise FileNotFoundError(f"MD final not found: {final_md}")

    md_text = final_md.read_text(encoding="utf-8")

    front = render_to_string("pipeline/frontispiece.md.j2", {"edition": edition})
    copyright_page = render_to_string("pipeline/copyright.md.j2", {"edition": edition})
    about_edition = render_to_string("pipeline/about_edition.md.j2", {"edition": edition})
    about_contrib = render_to_string("pipeline/about_contributor.md.j2", {"edition": edition})

    parts = [
        front.strip(),
        "",
        copyright_page.strip(),
        "",
        about_edition.strip(),
        "",
        about_contrib.strip(),
        "",
        md_text.strip(),
        "",
    ]
    build_text = "\n".join(parts)

    out_path = paths.build_md_path(edition)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_text, encoding="utf-8")

    return {
        "path": str(out_path),
        "preview": build_text[:2000],
    }
