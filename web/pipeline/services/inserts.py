from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class InsertSpec:
    image_dir: str
    default_pos: str
    chapters: Dict[int, List[str]]


def load_inserts_json(path: Path) -> Optional[InsertSpec]:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    image_dir = str(data.get("image_dir", "")).strip()
    default_pos = str(data.get("default_pos", "after_heading")).strip()
    chapters_raw = data.get("chapters", {}) or {}

    chapters: Dict[int, List[str]] = {}
    for key, value in chapters_raw.items():
        try:
            idx = int(str(key).strip())
        except ValueError:
            continue
        imgs = [str(x).strip() for x in (value or []) if str(x).strip()]
        if imgs:
            chapters[idx] = imgs

    if not image_dir or not chapters:
        return None
    return InsertSpec(image_dir=image_dir, default_pos=default_pos, chapters=chapters)


def inject_images_into_miolo_md(miolo_md: str, spec: InsertSpec) -> str:
    lines = miolo_md.splitlines()
    out: List[str] = []
    chapter_idx = 0

    for line in lines:
        out.append(line)
        if line.startswith("# ") or line.startswith("## "):
            chapter_idx += 1
            imgs = spec.chapters.get(chapter_idx)
            if imgs and spec.default_pos == "after_heading":
                out.append("")
                for img in imgs:
                    img_path = f"{spec.image_dir.rstrip('/')}/{img}"
                    out.append(f"![]({img_path})")
                    out.append("")

    return "\n".join(out).rstrip() + "\n"
