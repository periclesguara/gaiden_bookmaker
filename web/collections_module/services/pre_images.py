from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from django.utils.text import slugify

from gaiden.infrastructure import collections_storage

from collections_module.models import Collection, CollectionItem


VALID_IMAGE_TYPES = {"cover", "opening", "chapter", "divider"}


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _canonical_slug(value: str, fallback: str) -> str:
    slug = slugify(value or "")
    return slug.replace("-", "_") or fallback


def _book_code(collection: Collection) -> str:
    return collection.pipeline_book_code or collection.code


def pre_images_dir(collection: Collection) -> Path:
    return collections_storage.pre_images_dir(collection.code, collection.language)


def default_text_source(collection: Collection) -> Path:
    return collections_storage.merged_source_path(collection.code, collection.language)


def default_config(collection: Collection, *, text_source: Path | None = None) -> dict[str, Any]:
    return {
        "text_source": str(text_source or default_text_source(collection)),
        "book_code": _book_code(collection),
        "language": collection.language,
        "work_type": "collection",
        "image_policy": {
            "cover": True,
            "openings": True,
            "chapter_images": "selected",
            "dividers": "none",
        },
        "default_model": "gpt-image-1.5",
        "default_quality": "medium",
        "default_size": "1024x1536",
        "default_format": "jpg",
    }


def _heading_level(line: str) -> int | None:
    stripped = line.strip()
    if stripped.startswith("#"):
        return len(stripped) - len(stripped.lstrip("#"))
    if re.fullmatch(r"\*\*chapter\s+[^*]+\*\*", stripped, flags=re.I):
        return 2
    if re.fullmatch(r"chapter\s+[\wivxlcdm]+(?:\s*[—-].+)?", stripped, flags=re.I):
        return 2
    return None


def _heading_text(line: str) -> str:
    stripped = line.strip()
    stripped = stripped.lstrip("#").strip()
    stripped = stripped.strip("*").strip()
    return re.sub(r"\s+", " ", stripped)


def extract_structure(text: str, items: list[CollectionItem]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lines = text.splitlines()
    headings: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        level = _heading_level(line)
        if level is None:
            continue
        title = _heading_text(line)
        if not title:
            continue
        headings.append(
            {
                "order": len(headings) + 1,
                "line_number": index,
                "level": level,
                "text": title,
                "raw": line,
                "kind": "chapter" if re.match(r"chapter\s+", title, flags=re.I) else "work",
            }
        )

    if not headings and items:
        for item in items:
            headings.append(
                {
                    "order": len(headings) + 1,
                    "line_number": None,
                    "level": 1,
                    "text": item.work_title,
                    "raw": item.work_title,
                    "kind": "work",
                    "source": "collection_items",
                }
            )

    positions: list[dict[str, Any]] = []
    current_work: dict[str, Any] | None = None
    chapter_number_by_work: Counter[str] = Counter()
    for heading in headings:
        if heading["kind"] == "work":
            current_work = heading
            positions.append(
                {
                    **heading,
                    "work_title": heading["text"],
                    "work_order": sum(1 for item in positions if item["kind"] == "work") + 1,
                    "chapter_number": None,
                    "starts_at_line": heading["line_number"],
                    "ends_before_line": None,
                }
            )
            continue
        if current_work is None:
            current_work = {
                "text": "Main Text",
                "line_number": 1,
            }
        work_title = current_work["text"]
        chapter_number_by_work[work_title] += 1
        positions.append(
            {
                **heading,
                "work_title": work_title,
                "work_order": max(1, sum(1 for item in positions if item["kind"] == "work")),
                "chapter_number": chapter_number_by_work[work_title],
                "starts_at_line": heading["line_number"],
                "ends_before_line": None,
            }
        )
    for index, position in enumerate(positions[:-1]):
        position["ends_before_line"] = positions[index + 1]["starts_at_line"]
    return headings, positions


def build_manifest(config: dict[str, Any], positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    book_code = config["book_code"]
    language = config["language"]
    policy = config.get("image_policy") or {}
    images: list[dict[str, Any]] = []
    works = [p for p in positions if p["kind"] == "work"]

    if policy.get("cover"):
        images.append(
            {
                "filename": f"{book_code}_cover.jpg",
                "image_type": "cover",
                "work_order": None,
                "work_title": None,
                "work_slug": None,
                "chapter": None,
                "chapter_title": None,
                "canonical_order": 0,
                "book_code": book_code,
                "language": language,
            }
        )

    if policy.get("openings", True):
        for work in works:
            work_slug = _canonical_slug(work["work_title"], f"work_{work['work_order']:03d}")
            images.append(
                {
                    "filename": f"{book_code}_work_{work['work_order']:03d}_{work_slug}_opening.jpg",
                    "image_type": "opening",
                    "work_order": work["work_order"],
                    "work_title": work["work_title"],
                    "work_slug": work_slug,
                    "chapter": None,
                    "chapter_title": None,
                    "canonical_order": len(images),
                    "book_code": book_code,
                    "language": language,
                }
            )

    chapter_policy = policy.get("chapter_images", "selected")
    if chapter_policy in {"selected", "all"}:
        chapters = [p for p in positions if p["kind"] == "chapter"]
        for chapter in chapters:
            if chapter_policy == "selected" and chapter["chapter_number"] != 1:
                continue
            work_slug = _canonical_slug(chapter["work_title"], f"work_{chapter['work_order']:03d}")
            images.append(
                {
                    "filename": (
                        f"{book_code}_work_{chapter['work_order']:03d}_{work_slug}_"
                        f"chapter_{chapter['chapter_number']:02d}.jpg"
                    ),
                    "image_type": "chapter",
                    "work_order": chapter["work_order"],
                    "work_title": chapter["work_title"],
                    "work_slug": work_slug,
                    "chapter": chapter["chapter_number"],
                    "chapter_title": chapter["text"],
                    "canonical_order": len(images),
                    "book_code": book_code,
                    "language": language,
                }
            )
    return images


def build_anchors(images: list[dict[str, Any]], positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    by_work = {p["work_title"]: p for p in positions if p["kind"] == "work"}
    chapters = [p for p in positions if p["kind"] == "chapter"]
    for image in images:
        image_type = image["image_type"]
        if image_type == "cover":
            anchors.append(
                {
                    "filename": image["filename"],
                    "image_type": image_type,
                    "anchor_type": "external_cover",
                    "insert_after": None,
                    "insertion_position": "cover_asset_only",
                    "work_title": None,
                    "chapter_label": None,
                    "notes": "cover is not inserted in text",
                }
            )
            continue
        if image_type == "opening":
            work = by_work.get(image["work_title"], {})
            insert_after = f"# {image['work_title']}"
            anchors.append(
                {
                    "filename": image["filename"],
                    "image_type": image_type,
                    "anchor_type": "work_title",
                    "insert_after": insert_after,
                    "insertion_position": "after_work_title",
                    "work_title": image["work_title"],
                    "chapter_label": None,
                    "line_number": work.get("line_number"),
                    "notes": "insert after canonical work title",
                }
            )
            continue
        chapter = next(
            (
                p
                for p in chapters
                if p["work_title"] == image["work_title"]
                and p["chapter_number"] == image["chapter"]
            ),
            {},
        )
        anchors.append(
            {
                "filename": image["filename"],
                "image_type": image_type,
                "anchor_type": "chapter_heading",
                "insert_after": chapter.get("raw") or image.get("chapter_title"),
                "insertion_position": "after_chapter_heading",
                "work_title": image["work_title"],
                "chapter_label": image.get("chapter_title"),
                "line_number": chapter.get("line_number"),
                "notes": "insert after canonical chapter heading",
            }
        )
    return anchors


def build_briefs(images: list[dict[str, Any]], anchors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    anchors_by_filename = {item["filename"]: item for item in anchors}
    briefs: list[dict[str, Any]] = []
    for image in images:
        anchor = anchors_by_filename.get(image["filename"], {})
        image_type = image["image_type"]
        work_title = image.get("work_title") or "Full collection"
        chapter_label = image.get("chapter_title") or "—"
        focus = (
            "premium external cover composition"
            if image_type == "cover"
            else f"{work_title} atmospheric opening image"
            if image_type == "opening"
            else f"{work_title}, {chapter_label}"
        )
        briefs.append(
            {
                "filename": image["filename"],
                "image_type": image_type,
                "work_title": image.get("work_title"),
                "chapter_label": chapter_label,
                "insert_after": anchor.get("insert_after"),
                "visual_focus": focus,
                "main_subject": work_title,
                "setting": "period-appropriate literary atmosphere",
                "secondary_elements": "symbolic objects from the surrounding text",
                "mood": "atmospheric, readable, premium literary tone",
                "lighting": "moody but readable, bright midtones, lifted shadows",
                "avoid": "text, watermark, generic fantasy clutter, crushed blacks",
                "spoiler_level": "low",
            }
        )
    return briefs


def build_prompts(config: dict[str, Any], briefs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prompts: list[dict[str, Any]] = []
    for brief in briefs:
        prompt_base = (
            f"Create a premium illustrated {brief['image_type']} image for a literary book. "
            f"Visual focus: {brief['visual_focus']}. Main subject: {brief['main_subject']}. "
            f"Setting: {brief['setting']}. Secondary elements: {brief['secondary_elements']}. "
            f"Mood: {brief['mood']}. Lighting: {brief['lighting']}. "
            f"Avoid: {brief['avoid']}. No text in image."
        )
        prompts.append(
            {
                "filename": brief["filename"],
                "image_type": brief["image_type"],
                "work_title": brief["work_title"],
                "chapter_label": brief["chapter_label"],
                "prompt_base": prompt_base,
                "prompt_full": prompt_base,
                "size": config.get("default_size", "1024x1536"),
                "quality": config.get("default_quality", "medium"),
                "model": config.get("default_model", "gpt-image-1.5"),
                "format": config.get("default_format", "jpg"),
            }
        )
    return prompts


def validate_package(
    manifest: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    briefs: list[dict[str, Any]],
    prompts: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest_names = [item["filename"] for item in manifest]
    anchor_names = [item["filename"] for item in anchors]
    brief_names = [item["filename"] for item in briefs]
    prompt_names = [item["filename"] for item in prompts]
    duplicates = sorted(name for name, count in Counter(manifest_names).items() if count > 1)
    return {
        "total_images_expected": len(manifest),
        "total_covers": sum(1 for item in manifest if item["image_type"] == "cover"),
        "total_openings": sum(1 for item in manifest if item["image_type"] == "opening"),
        "total_chapters": sum(1 for item in manifest if item["image_type"] == "chapter"),
        "total_dividers": sum(1 for item in manifest if item["image_type"] == "divider"),
        "duplicated_filenames": duplicates,
        "missing_anchors": sorted(set(manifest_names) - set(anchor_names)),
        "missing_briefs": sorted(set(manifest_names) - set(brief_names)),
        "missing_prompts": sorted(set(manifest_names) - set(prompt_names)),
        "invalid_image_types": sorted(
            {item.get("image_type", "") for item in manifest if item.get("image_type") not in VALID_IMAGE_TYPES}
        ),
    }


def ready_for_generation(validation: dict[str, Any]) -> bool:
    return not any(
        validation[key]
        for key in ("duplicated_filenames", "missing_anchors", "missing_briefs", "missing_prompts", "invalid_image_types")
    )


def render_report(
    manifest: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    briefs: list[dict[str, Any]],
    prompts: list[dict[str, Any]],
    validation: dict[str, Any],
) -> str:
    anchors_by_filename = {item["filename"]: item for item in anchors}
    briefs_by_filename = {item["filename"]: item for item in briefs}
    prompts_by_filename = {item["filename"]: item for item in prompts}
    lines = [
        "PRE-IMAGES REPORT",
        "",
        f"total images expected: {validation['total_images_expected']}",
        f"total covers: {validation['total_covers']}",
        f"total openings: {validation['total_openings']}",
        f"total chapters: {validation['total_chapters']}",
        f"total dividers: {validation['total_dividers']}",
        f"ready_for_image_maker: {'yes' if ready_for_generation(validation) else 'no'}",
        "",
    ]
    for item in manifest:
        filename = item["filename"]
        anchor = anchors_by_filename.get(filename, {})
        brief = briefs_by_filename.get(filename, {})
        prompt = prompts_by_filename.get(filename, {})
        lines.extend(
            [
                "[ready_for_image_maker]",
                f"type: {item['image_type']}",
                f"filename: {filename}",
                f"work: {item.get('work_title') or '—'}",
                f"chapter: {item.get('chapter_title') or '—'}",
                f"insert_after: {anchor.get('insert_after') or '—'}",
                f"visual_focus: {brief.get('visual_focus') or '—'}",
                f"prompt_base: {prompt.get('prompt_base') or '—'}",
                "",
            ]
        )
    return "\n".join(lines)


def run_pre_images(collection: Collection, items: list[CollectionItem]) -> dict[str, Any]:
    out_dir = pre_images_dir(collection)
    config = default_config(collection)
    text_source = Path(config["text_source"])
    if not text_source.exists():
        raise FileNotFoundError(f"Pre-Images text_source not found: {text_source}")
    text = text_source.read_text(encoding="utf-8")
    headings, positions = extract_structure(text, items)
    if not headings:
        raise ValueError("Pre-Images blocked: headings list is empty.")
    manifest = build_manifest(config, positions)
    anchors = build_anchors(manifest, positions)
    briefs = build_briefs(manifest, anchors)
    prompts = build_prompts(config, briefs)
    validation = validate_package(manifest, anchors, briefs, prompts)
    report = render_report(manifest, anchors, briefs, prompts, validation)

    files = {
        "pre_images_config": _write_json(out_dir / "pre_images_config.json", config),
        "headings_list": _write_json(out_dir / "headings_list.json", headings),
        "headings_positions": _write_json(out_dir / "headings_positions.json", positions),
        "image_manifest": _write_json(out_dir / "image_manifest.json", manifest),
        "insertion_anchors": _write_json(out_dir / "insertion_anchors.json", anchors),
        "image_briefs": _write_json(out_dir / "image_briefs.json", briefs),
        "image_prompts": _write_json(out_dir / "image_prompts.json", prompts),
        "pre_images_report": _write_text(out_dir / "pre_images_report.txt", report),
    }
    return {
        "out_dir": out_dir,
        "files": files,
        "validation": validation,
        "ready_for_image_maker": ready_for_generation(validation),
    }


def pre_images_status(collection: Collection) -> dict[str, Any]:
    out_dir = pre_images_dir(collection)
    files = {
        name: out_dir / filename
        for name, filename in {
            "pre_images_config": "pre_images_config.json",
            "headings_list": "headings_list.json",
            "headings_positions": "headings_positions.json",
            "image_manifest": "image_manifest.json",
            "insertion_anchors": "insertion_anchors.json",
            "image_briefs": "image_briefs.json",
            "image_prompts": "image_prompts.json",
            "pre_images_report": "pre_images_report.txt",
        }.items()
    }
    return {
        "out_dir": out_dir,
        "files": files,
        "complete": all(path.exists() for path in files.values()),
        "report_preview": files["pre_images_report"].read_text(encoding="utf-8")[:4000]
        if files["pre_images_report"].exists()
        else "",
    }
