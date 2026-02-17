from __future__ import annotations

import hashlib
import io
import logging
import re
import shutil
from pathlib import Path
from typing import Iterable

from django.conf import settings

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_EXTS = {
    ".png",
    ".webp",
    ".gif",
    ".jpg",
    ".jpeg",
    ".jfif",
    ".bmp",
    ".tif",
    ".tiff",
}
NUMERIC_STEM_RE = re.compile(r"^\d{2}$")
PROCESSED_NAME_RE = re.compile(r"^(\d{2})\.jpg$")
INSERT_START_RE = re.compile(r"^<!--\s*GAIDEN_IMAGE_INSERT_START\s+(\d{2})\s*-->$")
INSERT_END_RE = re.compile(r"^<!--\s*GAIDEN_IMAGE_INSERT_END\s+(\d{2})\s*-->$")
CHAPTER_HEADING_RE = re.compile(
    r"^(chapter|cap[ií]tulo|chapitre|capitolo|kapitel)\b|^\d+[\.\s:-]|^[ivxlcdm]+[\.\s:-]",
    re.IGNORECASE,
)
HEADING_RE = re.compile(r"^#{1,2}\s+(.+?)\s*$")
JPEG_QUALITY = 92


def project_root() -> Path:
    return Path(settings.BASE_DIR).parent


def covers_dir(book_code: str, language: str) -> Path:
    return project_root() / "data" / "covers" / book_code / language


def images_root_dir(book_code: str, language: str) -> Path:
    return project_root() / "data" / "images" / book_code / language


def images_raw_dir(book_code: str, language: str) -> Path:
    return images_root_dir(book_code, language) / "raw"


def images_processed_dir(book_code: str, language: str) -> Path:
    return images_root_dir(book_code, language) / "processed"


def build_images_dir(book_code: str, language: str) -> Path:
    return project_root() / "data" / "builds" / book_code / language / "images"


def ensure_image_dirs(book_code: str, language: str) -> tuple[Path, Path]:
    raw_dir = images_raw_dir(book_code, language)
    processed_dir = images_processed_dir(book_code, language)
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir, processed_dir


def validate_numeric_image_filename(filename: str) -> bool:
    path = Path(filename)
    ext = path.suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        return False
    return bool(NUMERIC_STEM_RE.match(path.stem))


def numeric_stem_or_raise(filename: str) -> str:
    if not validate_numeric_image_filename(filename):
        raise ValueError("Image name must be numeric: 00, 01, 02...")
    return Path(filename).stem


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _render_jpg_bytes(image_path: Path) -> bytes:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError("Pillow is required for image conversion.") from exc

    with Image.open(image_path) as img:
        fixed = ImageOps.exif_transpose(img)
        has_alpha = fixed.mode in {"RGBA", "LA"} or (
            fixed.mode == "P" and "transparency" in fixed.info
        )
        if has_alpha:
            rgba = fixed.convert("RGBA")
            white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            white.alpha_composite(rgba)
            rgb = white.convert("RGB")
        else:
            rgb = fixed.convert("RGB")

        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return out.getvalue()


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)


def _scan_numeric_raw_images(raw_dir: Path) -> dict[int, Path]:
    mapping: dict[int, Path] = {}
    if not raw_dir.exists():
        return mapping
    for path in sorted(raw_dir.iterdir(), key=lambda p: p.name):
        if not path.is_file():
            continue
        if not validate_numeric_image_filename(path.name):
            raise ValueError(f"Invalid raw image filename: {path.name}")
        idx = int(path.stem)
        if idx in mapping:
            raise ValueError(f"Duplicate raw image number detected: {idx:02d}")
        mapping[idx] = path
    return mapping


def find_cover_original(book_code: str, language: str) -> Path | None:
    root = covers_dir(book_code, language)
    if not root.exists():
        return None
    files = [p for p in sorted(root.glob("cover_original.*")) if p.is_file()]
    return files[-1] if files else None


def find_cover_source(book_code: str, language: str) -> Path | None:
    """Prefer cover_original.*, fallback to legacy cover.* files."""
    source = find_cover_original(book_code, language)
    if source:
        return source
    root = covers_dir(book_code, language)
    if root.exists():
        legacy: list[Path] = []
        for path in sorted(root.glob("cover.*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in ALLOWED_IMAGE_EXTS:
                continue
            legacy.append(path)
        if legacy:
            return legacy[-1]

    # Global fallback: search any language folder for this book.
    book_root = project_root() / "data" / "covers" / book_code
    if not book_root.exists():
        return None
    candidates: list[Path] = []
    for lang_dir in sorted(book_root.iterdir(), key=lambda p: p.name):
        if not lang_dir.is_dir():
            continue
        for pattern in ("cover_original.*", "cover.*"):
            for path in sorted(lang_dir.glob(pattern)):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in ALLOWED_IMAGE_EXTS:
                    continue
                candidates.append(path)
    return candidates[-1] if candidates else None


def convert_cover_to_jpg(book_code: str, language: str) -> dict:
    source = find_cover_source(book_code, language)
    if not source:
        return {
            "converted": False,
            "skipped": True,
            "reason": "cover_source_missing",
            "cover_jpg_path": "",
        }

    target = covers_dir(book_code, language) / "cover.jpg"
    payload = _render_jpg_bytes(source)
    payload_hash = _sha256_bytes(payload)
    skipped = False
    converted = True
    if target.exists() and _sha256_file(target) == payload_hash:
        skipped = True
        converted = False
    else:
        _atomic_write_bytes(target, payload)

    rel = target.relative_to(project_root()).as_posix()
    return {
        "converted": converted,
        "skipped": skipped,
        "cover_jpg_path": rel,
        "source": source.as_posix(),
    }


def convert_raw_images_to_processed(book_code: str, language: str) -> dict:
    raw_dir, processed_dir = ensure_image_dirs(book_code, language)
    raw_map = _scan_numeric_raw_images(raw_dir)

    converted_count = 0
    skipped_count = 0
    written_names: set[str] = set()

    for idx in sorted(raw_map.keys()):
        source = raw_map[idx]
        target_name = f"{idx:02d}.jpg"
        target = processed_dir / target_name
        payload = _render_jpg_bytes(source)
        payload_hash = _sha256_bytes(payload)
        written_names.add(target_name)
        if target.exists() and _sha256_file(target) == payload_hash:
            skipped_count += 1
            continue
        _atomic_write_bytes(target, payload)
        converted_count += 1

    if processed_dir.exists():
        for entry in list(processed_dir.iterdir()):
            if not entry.is_file():
                shutil.rmtree(entry, ignore_errors=True)
                continue
            name_match = PROCESSED_NAME_RE.match(entry.name)
            if not name_match:
                entry.unlink(missing_ok=True)
                continue
            if entry.name not in written_names:
                entry.unlink(missing_ok=True)

    processed_files = sorted(
        p.name for p in processed_dir.glob("*.jpg") if PROCESSED_NAME_RE.match(p.name)
    )
    logger.info(
        "Image conversion finished for %s/%s: total=%s converted=%s skipped=%s",
        book_code,
        language,
        len(raw_map),
        converted_count,
        skipped_count,
    )
    return {
        "raw_count": len(raw_map),
        "converted_count": converted_count,
        "skipped_count": skipped_count,
        "processed_files": processed_files,
        "processed_dir": processed_dir.as_posix(),
    }


def list_processed_numbers(book_code: str, language: str) -> list[int]:
    processed_dir = images_processed_dir(book_code, language)
    if not processed_dir.exists():
        return []
    numbers: list[int] = []
    for path in sorted(processed_dir.iterdir(), key=lambda p: p.name):
        if not path.is_file():
            continue
        match = PROCESSED_NAME_RE.match(path.name)
        if not match:
            continue
        numbers.append(int(match.group(1)))
    return numbers


def _strip_previous_blocks(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        start = INSERT_START_RE.match(lines[i].strip())
        if not start:
            out.append(lines[i])
            i += 1
            continue
        while out and not out[-1].strip():
            out.pop()
        marker = start.group(1)
        i += 1
        while i < len(lines):
            end = INSERT_END_RE.match(lines[i].strip())
            if end and end.group(1) == marker:
                i += 1
                break
            i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        if out and i < len(lines):
            out.append("")
    return out


def _image_block_lines(num: int) -> list[str]:
    tag = f"{num:02d}"
    return [
        f"<!-- GAIDEN_IMAGE_INSERT_START {tag} -->",
        f"![](images/{tag}.jpg)",
        f"<!-- GAIDEN_IMAGE_INSERT_END {tag} -->",
        "",
    ]


def _chapter_line_map(lines: list[str]) -> dict[int, int]:
    chapter_map: dict[int, int] = {}
    chapter_idx = 0
    matched_any = False

    for line_idx, line in enumerate(lines):
        match = HEADING_RE.match(line.strip())
        if not match:
            continue
        heading = match.group(1).strip()
        if CHAPTER_HEADING_RE.search(heading):
            chapter_idx += 1
            chapter_map[chapter_idx] = line_idx
            matched_any = True

    if matched_any:
        return chapter_map

    chapter_idx = 0
    for line_idx, line in enumerate(lines):
        if HEADING_RE.match(line.strip()):
            chapter_idx += 1
            chapter_map[chapter_idx] = line_idx
    return chapter_map


def insert_images_deterministically(md_text: str, image_numbers: Iterable[int]) -> tuple[str, int, list[str]]:
    source_lines = _strip_previous_blocks(md_text.splitlines())
    chapter_map = _chapter_line_map(source_lines)
    before_map: dict[int, list[list[str]]] = {}
    prepend_blocks: list[list[str]] = []
    append_blocks: list[list[str]] = []
    warnings: list[str] = []

    ordered_numbers = sorted(set(int(n) for n in image_numbers))
    for num in ordered_numbers:
        block = _image_block_lines(num)
        if num == 0:
            prepend_blocks.append(block)
            continue
        target_line = chapter_map.get(num)
        if target_line is None:
            warning = (
                f"REPORT_V2_DEBUG image {num:02d} has no chapter {num}; appended at document end."
            )
            logger.warning(warning)
            warnings.append(warning)
            append_blocks.append(block)
            continue
        before_map.setdefault(target_line, []).append(block)

    out: list[str] = []
    for block in prepend_blocks:
        out.extend(block)

    for idx, line in enumerate(source_lines):
        for block in before_map.get(idx, []):
            out.extend(block)
        out.append(line)

    if append_blocks:
        if out and out[-1].strip():
            out.append("")
        for block in append_blocks:
            out.extend(block)

    while out and not out[-1].strip():
        out.pop()
    final = "\n".join(out).rstrip() + "\n"
    return final, len(ordered_numbers), warnings


def apply_processed_images_to_miolo(
    book_code: str,
    language: str,
    md_path: Path,
) -> dict:
    if not md_path.exists():
        raise FileNotFoundError(f"Markdown file not found for insertion: {md_path}")

    numbers = list_processed_numbers(book_code, language)
    current = md_path.read_text(encoding="utf-8")
    updated, inserted_count, warnings = insert_images_deterministically(current, numbers)
    md_path.write_text(updated, encoding="utf-8")
    return {
        "inserted_images_count": inserted_count,
        "processed_numbers": [f"{n:02d}" for n in numbers],
        "warnings": warnings,
        "md_path": md_path.as_posix(),
    }


def sync_processed_images_into_build(book_code: str, language: str) -> dict:
    processed_dir = images_processed_dir(book_code, language)
    build_dir = build_images_dir(book_code, language)
    build_dir.mkdir(parents=True, exist_ok=True)

    expected: set[str] = set()
    if processed_dir.exists():
        for path in sorted(processed_dir.iterdir(), key=lambda p: p.name):
            if not path.is_file():
                continue
            if not PROCESSED_NAME_RE.match(path.name):
                continue
            expected.add(path.name)
            shutil.copy2(path, build_dir / path.name)

    for existing in list(build_dir.iterdir()):
        if not existing.is_file():
            shutil.rmtree(existing, ignore_errors=True)
            continue
        if existing.name not in expected:
            existing.unlink(missing_ok=True)

    return {
        "build_images_dir": build_dir.as_posix(),
        "image_count": len(expected),
        "images": sorted(expected),
    }
