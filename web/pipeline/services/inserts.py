from __future__ import annotations

import json
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class InsertSpec:
    image_dir: str
    default_pos: str
    chapters: Dict[int, List[str]]
    post_cover: List[str] = field(default_factory=list)


_ALLOWED_IMAGE_EXTS = {".png", ".webp", ".tif", ".tiff", ".bmp", ".jpg", ".jpeg"}
_CHAPTER_DIR_RE = re.compile(r"^\d{2}$")
_MANUAL_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)|<\s*img\b|<\s*figure\b", re.IGNORECASE)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_MD_HEADING_RE = re.compile(r"^#{1,2}\s+")

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_image_dir(image_dir: str) -> Path:
    path = Path(image_dir)
    if path.is_absolute():
        return path
    return (_project_root() / path).resolve()


def _ensure_safe_relpath(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"Image path must be relative: {value}")
    if ".." in path.parts:
        raise ValueError(f"Image path traversal is not allowed: {value}")
    return path


def _normalize_image(image_dir: Path, rel_path: Path) -> str:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError(
            "Pillow is required to normalize images for illustrated mode."
        ) from exc

    src = image_dir / rel_path
    if not src.exists():
        raise FileNotFoundError(f"Image not found: {src}")
    if src.is_dir():
        raise ValueError(f"Image path is a directory: {src}")

    ext = src.suffix.lower()
    if ext not in _ALLOWED_IMAGE_EXTS:
        raise ValueError(f"Unsupported image format: {src.name}")

    target = src.with_suffix(".jpg")
    if src.resolve() != target.resolve() and target.exists():
        raise ValueError(
            f"Conflict normalizing {src.name}: target already exists ({target.name})."
        )

    with Image.open(src) as img:
        fixed = ImageOps.exif_transpose(img)
        rgb = fixed.convert("RGB")
        tmp = target.with_suffix(".jpg.tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        rgb.save(tmp, format="JPEG", quality=95, optimize=True)
        tmp.replace(target)

    if src.resolve() != target.resolve():
        src.unlink()

    return target.relative_to(image_dir).as_posix()


def _normalize_images_dir(image_dir: Path) -> None:
    for path in sorted(image_dir.rglob("*")):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext in _ALLOWED_IMAGE_EXTS:
            rel = path.relative_to(image_dir)
            _normalize_image(image_dir, rel)


def normalize_image_dir(image_dir: Path) -> None:
    _normalize_images_dir(image_dir)
    _validate_image_dir_structure(image_dir)
    logger.info("Illustrated images normalized to JPG in %s", image_dir)


def _normalize_spec_images(image_dir: Path, images: List[str]) -> List[str]:
    normalized: List[str] = []
    for img in images:
        rel = _ensure_safe_relpath(img)
        target = (image_dir / rel).with_suffix(".jpg")
        if not target.exists():
            raise FileNotFoundError(f"Normalized image not found: {target}")
        normalized.append(target.relative_to(image_dir).as_posix())
    return normalized


def _build_image_name(rel: str) -> str:
    rel_path = Path(rel)
    if len(rel_path.parts) < 2:
        raise ValueError(f"Invalid image path depth: {rel}")
    folder = rel_path.parts[0]
    stem = rel_path.stem
    return f"{folder}_{stem}.jpg"


def _build_image_ref(rel: str) -> str:
    return f"images/{_build_image_name(rel)}"


def _build_image_map(spec: InsertSpec) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    used: set[str] = set()
    all_rels = list(spec.post_cover)
    for imgs in spec.chapters.values():
        all_rels.extend(imgs)
    for rel in all_rels:
        name = _build_image_name(rel)
        if name in used:
            raise ValueError(f"Image name collision in build: {name}")
        used.add(name)
        mapping[rel] = name
    return mapping


def _natural_sort_key(value: str) -> list[object]:
    parts = re.split(r"(\d+)", value)
    key: list[object] = []
    for part in parts:
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part.lower())
    return key


def _validate_image_dir_structure(image_dir: Path) -> None:
    for entry in sorted(image_dir.iterdir(), key=lambda p: p.name):
        if entry.is_file():
            raise ValueError(f"Images must live in chapter folders (00..NN): {entry.name}")
        if entry.is_dir():
            if entry.name.startswith("."):
                continue
            if not _CHAPTER_DIR_RE.match(entry.name):
                raise ValueError(f"Unknown chapter folder: {entry.name}")
            for child in sorted(entry.iterdir(), key=lambda p: p.name):
                if child.is_dir():
                    raise ValueError(f"Nested folder not allowed in {entry.name}: {child.name}")
                if child.suffix.lower() != ".jpg":
                    raise ValueError(f"Non-JPG file found in {entry.name}: {child.name}")


def _validate_rel_path_structure(rel_path: Path, expected_folder: str) -> None:
    if len(rel_path.parts) != 2:
        raise ValueError(f"Invalid image path depth: {rel_path.as_posix()}")
    if rel_path.parts[0] != expected_folder:
        raise ValueError(
            f"Image path folder mismatch: expected {expected_folder}/ got {rel_path.as_posix()}"
        )


def _auto_scan_images(image_dir: Path) -> tuple[Dict[int, List[str]], List[str]]:
    chapters: Dict[int, List[str]] = {}
    post_cover: List[str] = []
    for folder in sorted(image_dir.iterdir(), key=lambda p: p.name):
        if not folder.is_dir():
            continue
        if not _CHAPTER_DIR_RE.match(folder.name):
            continue
        idx = int(folder.name)
        items = [
            p
            for p in sorted(folder.iterdir(), key=lambda p: _natural_sort_key(p.name))
            if p.is_file() and p.suffix.lower() == ".jpg"
        ]
        if not items:
            continue
        rels = [p.relative_to(image_dir).as_posix() for p in items]
        if idx == 0:
            post_cover = rels
        else:
            chapters[idx] = rels
    return chapters, post_cover


def _validate_no_manual_images(miolo_md: str) -> None:
    if _MANUAL_IMAGE_RE.search(miolo_md):
        raise ValueError(
            "Illustrated mode forbids manual image tags. "
            "Remove markdown/HTML images from the text and use inserts.json only."
        )


def load_inserts_json(path: Path) -> Optional[InsertSpec]:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    image_dir = str(data.get("image_dir", "")).strip()
    default_pos = str(data.get("default_pos", "after_heading")).strip()
    chapters_raw = data.get("chapters", {}) or {}
    post_cover_raw = (
        data.get("post_cover")
        or data.get("post_cover_images")
        or []
    )

    def _dedupe(values: List[str]) -> List[str]:
        seen: set[str] = set()
        ordered: List[str] = []
        for item in values:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered

    chapters: Dict[int, List[str]] = {}
    post_cover: List[str] = []
    for key, value in chapters_raw.items():
        try:
            idx = int(str(key).strip())
        except ValueError:
            continue
        imgs = [str(x).strip() for x in (value or []) if str(x).strip()]
        if not imgs:
            continue
        if idx == 0:
            post_cover.extend(imgs)
        else:
            chapters[idx] = _dedupe(imgs)

    post_cover += [str(x).strip() for x in (post_cover_raw or []) if str(x).strip()]
    post_cover = _dedupe(post_cover)

    if not image_dir:
        return None

    image_dir_path = _resolve_image_dir(image_dir)
    if not image_dir_path.exists():
        raise FileNotFoundError(f"Images folder not found: {image_dir_path}")
    if not image_dir_path.is_dir():
        raise ValueError(f"Images folder is not a directory: {image_dir_path}")

    _normalize_images_dir(image_dir_path)
    _validate_image_dir_structure(image_dir_path)
    logger.info("Illustrated images normalized to JPG in %s", image_dir_path)
    if not chapters and not post_cover:
        chapters, post_cover = _auto_scan_images(image_dir_path)
    if not chapters and not post_cover:
        return None

    normalized_post_cover = _normalize_spec_images(image_dir_path, post_cover)
    normalized_chapters: Dict[int, List[str]] = {}
    for idx, imgs in chapters.items():
        normalized_chapters[idx] = _normalize_spec_images(image_dir_path, imgs)

    for rel in normalized_post_cover:
        _validate_rel_path_structure(Path(rel), "00")
    for idx, imgs in normalized_chapters.items():
        expected_folder = f"{idx:02d}"
        for rel in imgs:
            _validate_rel_path_structure(Path(rel), expected_folder)

    return InsertSpec(
        image_dir=str(image_dir_path),
        default_pos=default_pos,
        chapters=normalized_chapters,
        post_cover=normalized_post_cover,
    )


def prepare_build_images(build_dir: Path, spec: InsertSpec) -> Path:
    image_dir = Path(spec.image_dir)
    dest_root = build_dir / "images"
    if dest_root.exists():
        for item in dest_root.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    dest_root.mkdir(parents=True, exist_ok=True)

    name_map = _build_image_map(spec)

    for rel in sorted(name_map.keys()):
        rel_path = _ensure_safe_relpath(rel)
        src = image_dir / rel_path
        if not src.exists():
            raise FileNotFoundError(f"Normalized image not found: {src}")
        if src.suffix.lower() != ".jpg":
            raise ValueError(f"Non-JPG image detected in build: {src.name}")
        dest = dest_root / name_map[rel]
        shutil.copy2(src, dest)

    expected_names = set(name_map.values())
    for path in dest_root.iterdir():
        if path.is_dir():
            raise ValueError(f"Unexpected directory in build images: {path.name}")
        if path.name not in expected_names:
            raise ValueError(f"Unexpected image in build images: {path.name}")
        if path.suffix.lower() != ".jpg":
            raise ValueError(f"Non-JPG artifact in build images directory: {path.name}")

    return dest_root


def build_post_cover_blocks(spec: InsertSpec) -> str:
    if not spec.post_cover:
        return ""
    blocks: List[str] = []
    for rel in spec.post_cover:
        src = _build_image_ref(rel)
        blocks.append('<div class="page-break"></div>')
        blocks.append("")
        blocks.append('<figure class="post-cover-illustration">')
        blocks.append(f"![Post-cover Illustration]({src})")
        blocks.append("</figure>")
        blocks.append("")
        blocks.append('<div class="page-break"></div>')
        blocks.append("")
    return "\n".join(blocks).rstrip()


def validate_illustrated_miolo(miolo_md: str, spec: InsertSpec) -> None:
    lines = [ln.rstrip() for ln in miolo_md.splitlines()]

    expected_chapters = set(spec.chapters.keys())
    ordered_chapters = sorted(expected_chapters)
    expected_images = {_build_image_ref(rel) for imgs in spec.chapters.values() for rel in imgs}

    chapter_breaks = 0
    chapter_starts = 0
    figure_blocks = 0
    expected_chapter_count = len(expected_chapters)
    expected_figure_count = sum(len(imgs) for imgs in spec.chapters.values())

    i = 0
    block_idx = 0
    while i < len(lines):
        line = lines[i].strip()
        if line != '<div class="chapter-break"></div>':
            i += 1
            continue
        chapter_breaks += 1
        block_idx += 1
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1

        found_any = False
        while i < len(lines) and lines[i].strip() == '<figure class="chapter-illustration">':
            figure_blocks += 1
            found_any = True
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i >= len(lines):
                raise ValueError("Invalid illustration block: missing image line.")
            image_line = lines[i].strip()
            if not (image_line.startswith("<img ") or _MD_IMAGE_RE.match(image_line)):
                raise ValueError("Invalid illustration img line.")
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i >= len(lines) or lines[i].strip() != "</figure>":
                raise ValueError("Invalid illustration block: missing closing </figure>.")
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1

        if not found_any:
            raise ValueError("Chapter-break found without illustration block.")

        if i >= len(lines) or lines[i].strip() != '<div class="chapter-start"></div>':
            raise ValueError("Missing chapter-start after illustration block.")
        chapter_starts += 1
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines) or not _MD_HEADING_RE.match(lines[i].strip()):
            raise ValueError("Missing chapter heading after chapter-start.")
        if block_idx <= len(ordered_chapters):
            expected_id = f"{{#chapter-{ordered_chapters[block_idx - 1]:02d}}}"
            if expected_id not in lines[i]:
                raise ValueError("Chapter heading missing required chapter id attribute.")

    if chapter_breaks > expected_chapter_count:
        raise ValueError("Duplicate chapter-break blocks detected.")
    if chapter_starts > expected_chapter_count:
        raise ValueError("Duplicate chapter-start blocks detected.")
    if figure_blocks > expected_figure_count:
        raise ValueError("Duplicate illustration figures detected.")
    if chapter_breaks < expected_chapter_count or figure_blocks < expected_figure_count:
        logger.warning("Illustrated injection missing blocks; check chapter headings.")

    img_srcs = re.findall(r'<img\s+[^>]*src="([^"]+)"', miolo_md, flags=re.IGNORECASE)
    img_srcs += [m.group(1) for m in _MD_IMAGE_RE.finditer(miolo_md)]
    if img_srcs:
        if len(img_srcs) != len(set(img_srcs)):
            raise ValueError("Duplicate image srcs found in miolo.")
        invalid_srcs = [src for src in img_srcs if not src.startswith("images/") or not src.lower().endswith(".jpg")]
        if invalid_srcs:
            raise ValueError(f"Invalid image src in miolo: {', '.join(sorted(invalid_srcs))}")
        extra_srcs = set(img_srcs) - expected_images
        if extra_srcs:
            raise ValueError(f"Unexpected image srcs in miolo: {', '.join(sorted(extra_srcs))}")
        missing_srcs = expected_images - set(img_srcs)
        if missing_srcs:
            logger.warning("Illustrated images not injected into miolo: %s", sorted(missing_srcs))
        if len(img_srcs) > len(expected_images):
            raise ValueError("Miolo contains more images than specified.")


def validate_epub_images(epub_path: Path, spec: InsertSpec) -> int:
    expected_images = {_build_image_ref(rel) for rel in spec.post_cover}
    for imgs in spec.chapters.values():
        for rel in imgs:
            expected_images.add(_build_image_ref(rel))

    with zipfile.ZipFile(epub_path, "r") as zin:
        names = zin.namelist()
        opf_path = "EPUB/content.opf"
        if opf_path not in names:
            raise ValueError("EPUB content.opf not found for image validation.")
        opf_text = zin.read(opf_path).decode("utf-8", errors="replace")

        manifest_images: set[str] = set()
        cover_images: set[str] = set()
        for match in re.finditer(r"<item[^>]+media-type=\"image/[^\">]+\"[^>]*>", opf_text):
            tag = match.group(0)
            href_match = re.search(r'href="([^"]+)"', tag)
            if href_match:
                href = href_match.group(1)
                manifest_images.add(href)
                if "cover-image" in tag or "cover" in href.lower():
                    cover_images.add(href)
            id_match = re.search(r'id="([^"]+)"', tag)
            if id_match and "cover" in id_match.group(1).lower() and href_match:
                cover_images.add(href_match.group(1))

        all_images = manifest_images | cover_images
        if not all_images:
            raise ValueError("No images found in EPUB manifest.")

        non_jpg = [img for img in all_images if not img.lower().endswith(".jpg")]
        if non_jpg:
            raise ValueError(f"Non-JPG images found in EPUB: {', '.join(sorted(non_jpg))}")

        missing = expected_images - all_images
        if missing:
            raise ValueError(f"Missing images in EPUB: {', '.join(sorted(missing))}")

        expected_with_cover = expected_images | cover_images
        extra = all_images - expected_with_cover
        if extra:
            raise ValueError(f"Unexpected images in EPUB: {', '.join(sorted(extra))}")

        zip_images = {
            name[len("EPUB/"):]
            for name in names
            if name.startswith("EPUB/images/") and not name.endswith("/")
        }
        if any(not img.lower().endswith(".jpg") for img in zip_images):
            raise ValueError("Non-JPG files found in EPUB/images/ directory.")
        extra_zip = zip_images - expected_with_cover
        if extra_zip:
            raise ValueError(f"Unexpected image files in EPUB: {', '.join(sorted(extra_zip))}")

        for rel in expected_images:
            epub_path_ref = f"EPUB/{rel}"
            if epub_path_ref not in names:
                raise ValueError(f"EPUB missing image file: {epub_path_ref}")

    logger.info("Illustrated EPUB validation OK. JPG count=%s", len(expected_images))
    return len(expected_images)


def rewrite_epub_illustrated_images(
    epub_path: Path,
    spec: InsertSpec,
    build_dir: Path,
) -> int:
    ordered: List[str] = []
    ordered.extend(spec.post_cover)
    for idx in sorted(spec.chapters.keys()):
        ordered.extend(spec.chapters[idx])

    if not ordered:
        return 0

    expected_images = [_build_image_ref(rel) for rel in ordered]
    allowed_alts = {"Post-cover Illustration", "Chapter Illustration"}
    chapter_blocks: dict[str, str] = {}
    for chapter_idx, imgs in spec.chapters.items():
        parts: list[str] = []
        parts.append('<div class="chapter-break"></div>')
        parts.append("")
        for rel in imgs:
            src = f"../{_build_image_ref(rel)}"
            parts.append('<figure class="chapter-illustration">')
            parts.append(f'<img src="{src}" alt="Chapter Illustration" />')
            parts.append("</figure>")
            parts.append("")
        parts.append('<div class="chapter-start"></div>')
        parts.append("")
        chapter_blocks[f"chapter-{chapter_idx:02d}"] = "\n".join(parts)

    with zipfile.ZipFile(epub_path, "r") as zin:
        names = zin.namelist()
        xhtml_names = sorted(
            n for n in names if n.startswith("EPUB/text/") and n.endswith(".xhtml")
        )
        updated_xhtml: dict[str, str] = {}
        old_hrefs: list[str] = []
        cursor = 0
        inserted_ids: set[str] = set()
        for name in xhtml_names:
            text = zin.read(name).decode("utf-8", errors="replace")

            def _replace_img(match: re.Match[str]) -> str:
                nonlocal cursor
                tag = match.group(0)
                alt_match = re.search(r'alt="([^"]*)"', tag)
                src_match = re.search(r'src="([^"]+)"', tag)
                if not alt_match or not src_match:
                    return tag
                alt = alt_match.group(1)
                if alt not in allowed_alts:
                    return tag
                if cursor >= len(expected_images):
                    raise ValueError("More illustrated images in EPUB than expected.")
                old_src = src_match.group(1)
                old_hrefs.append(old_src.lstrip("../"))
                new_src = f"../{expected_images[cursor]}"
                cursor += 1
                return f'<img src="{new_src}" alt="{alt}" />'

            text = re.sub(r"<img\s+[^>]*>", _replace_img, text)

            for class_name in ("post-cover-illustration", "chapter-illustration"):
                pattern = re.compile(
                    rf'<figure class="{class_name}">\s*(?:<figure>\s*)?(?:<p>\s*)?<img\s+[^>]*>\s*(?:</p>\s*)?(?:<figcaption[^>]*>.*?</figcaption>\s*)?(?:</figure>\s*)?</figure>',
                    flags=re.IGNORECASE | re.DOTALL,
                )

                def _flatten(match: re.Match[str]) -> str:
                    img_match = re.search(r"<img\s+[^>]*>", match.group(0))
                    img_tag = img_match.group(0) if img_match else ""
                    return f'<figure class="{class_name}">\n{img_tag}\n</figure>'

                text = pattern.sub(_flatten, text)

            chapter_block_re = re.compile(
                r'<div\s+class="chapter-break"[^>]*>.*?</div>\s*'
                r'(?:<figure\s+class="chapter-illustration">.*?</figure>\s*)+'
                r'<div\s+class="chapter-start"[^>]*>.*?</div>',
                flags=re.IGNORECASE | re.DOTALL,
            )
            text = chapter_block_re.sub("", text)

            for chapter_id, block in chapter_blocks.items():
                section_re = re.compile(
                    rf'(<section[^>]*id="{re.escape(chapter_id)}"[^>]*>)',
                    flags=re.IGNORECASE,
                )
                match = section_re.search(text)
                if not match:
                    continue
                text = section_re.sub(lambda m: m.group(1) + "\n" + block, text, count=1)
                sec_idx = match.end()
                h_match = re.search(r"<h1[^>]*>", text[sec_idx:], flags=re.IGNORECASE)
                if h_match:
                    tag = h_match.group(0)
                    new_tag = re.sub(r'id="[^"]*"', "", tag)
                    if 'id="' in new_tag:
                        new_tag = re.sub(r'id="[^"]*"', f'id="{chapter_id}"', new_tag)
                    else:
                        new_tag = new_tag[:-1] + f' id="{chapter_id}">'
                    start = sec_idx + h_match.start()
                    end = sec_idx + h_match.end()
                    text = text[:start] + new_tag + text[end:]
                inserted_ids.add(chapter_id)

            updated_xhtml[name] = text

        if cursor != len(expected_images):
            raise ValueError(
                f"Illustrated images mismatch: expected {len(expected_images)}, found {cursor}."
            )
        missing_blocks = set(chapter_blocks.keys()) - inserted_ids
        if missing_blocks:
            raise ValueError("Chapter image blocks not inserted in EPUB.")

        opf_path = "EPUB/content.opf"
        if opf_path not in names:
            raise ValueError("EPUB content.opf not found for image rewrite.")
        opf_text = zin.read(opf_path).decode("utf-8", errors="replace")
        opf_lines = opf_text.splitlines()
        kept_lines: list[str] = []
        existing_hrefs: set[str] = set()

        image_item_re = re.compile(r"<item[^>]+media-type=\"image/[^\">]+\"[^>]*>")
        href_re = re.compile(r'href="([^"]+)"')

        for line in opf_lines:
            if image_item_re.search(line):
                href_match = href_re.search(line)
                href = href_match.group(1) if href_match else ""
                if href and href in old_hrefs:
                    continue
                if href:
                    existing_hrefs.add(href)
            kept_lines.append(line)

        insert_idx = None
        for idx, line in enumerate(kept_lines):
            if "</manifest>" in line:
                insert_idx = idx
                break
        if insert_idx is None:
            raise ValueError("EPUB manifest not found for image rewrite.")

        new_items: list[str] = []
        for i, rel in enumerate(expected_images, start=1):
            if rel in existing_hrefs:
                continue
            new_items.append(
                f'    <item id="ill_img_{i:04d}" href="{rel}" media-type="image/jpeg" />'
            )

        kept_lines[insert_idx:insert_idx] = new_items
        opf_text = "\n".join(kept_lines)

        temp_path = epub_path.with_suffix(".tmp.epub")
        with zipfile.ZipFile(temp_path, "w") as zout:
            for info in zin.infolist():
                name = info.filename
                if name == opf_path:
                    zout.writestr(name, opf_text)
                    continue
                if name in updated_xhtml:
                    zout.writestr(name, updated_xhtml[name])
                    continue
                if name.startswith("EPUB/") and name[len("EPUB/"):] in old_hrefs:
                    continue
                if name.startswith("EPUB/images/") and name[len("EPUB/"):] in expected_images:
                    continue
                data = zin.read(name)
                zout.writestr(info, data)

            image_root = build_dir / "images"
            for rel in expected_images:
                src = image_root / Path(rel).relative_to("images")
                if not src.exists():
                    raise FileNotFoundError(f"Build image missing: {src}")
                zout.write(src, f"EPUB/{rel}")

        temp_path.replace(epub_path)

    logger.info("Illustrated EPUB rewrite OK. JPG count=%s", len(expected_images))
    return len(expected_images)


def inject_images_into_miolo_md(miolo_md: str, spec: InsertSpec) -> str:
    _validate_no_manual_images(miolo_md)
    lines = miolo_md.splitlines()
    out: List[str] = []
    chapter_idx = 0
    inserted: set[int] = set()
    total_headings = 0

    for line in lines:
        if line.startswith("# ") or line.startswith("## "):
            chapter_idx += 1
            total_headings += 1
            imgs = spec.chapters.get(chapter_idx)
            if imgs:
                out.append('<div class="chapter-break"></div>')
                out.append("")
                for img in imgs:
                    src = _build_image_ref(img)
                    out.append('<figure class="chapter-illustration">')
                    out.append(f"![Chapter Illustration]({src})")
                    out.append("</figure>")
                    out.append("")
                out.append('<div class="chapter-start"></div>')
                out.append("")
                inserted.add(chapter_idx)
                if "{#chapter-" not in line:
                    line = f"{line} {{#chapter-{chapter_idx:02d}}}"
        out.append(line)

    if spec.chapters and not inserted:
        logger.warning("Illustrated images found but no chapter headings for injection.")
    else:
        missing = sorted(set(spec.chapters.keys()) - inserted)
        if missing:
            logger.warning("Illustrated images without injection points: %s", missing)
    if inserted:
        logger.info("Illustrated images injected for %s chapters.", len(inserted))

    return "\n".join(out).rstrip() + "\n"
