from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from gaiden.infrastructure import collections_storage

from collections_module.models import Collection
from collections_module.services import pre_images


def image_maker_dir(collection: Collection) -> Path:
    return collections_storage.image_maker_dir(collection.code, collection.language)


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _package_from_pre_images(collection: Collection) -> dict[str, Any]:
    status = pre_images.pre_images_status(collection)
    files = status["files"]
    return {
        "image_manifest": _load_json(files["image_manifest"]),
        "insertion_anchors": _load_json(files["insertion_anchors"]),
        "image_prompts": _load_json(files["image_prompts"]),
        "image_briefs": _load_json(files["image_briefs"]) if files["image_briefs"].exists() else [],
        "pre_images_report": files["pre_images_report"].read_text(encoding="utf-8")
        if files["pre_images_report"].exists()
        else "",
    }


def parse_pasted_package(raw: str, collection: Collection) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return _package_from_pre_images(collection)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Image-Maker package is not valid JSON: {exc}") from exc
    if isinstance(payload, list):
        raise ValueError("Image-Maker package must be a JSON object, not a list.")
    if not isinstance(payload, dict):
        raise ValueError("Image-Maker package must be a JSON object.")
    return payload


def _records(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if isinstance(value, dict) and isinstance(value.get("images"), list):
        value = value["images"]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def validate_package(payload: dict[str, Any]) -> dict[str, Any]:
    manifest = _records(payload, "image_manifest")
    anchors = _records(payload, "insertion_anchors")
    prompts = _records(payload, "image_prompts")
    filenames = [item.get("filename") for item in manifest if item.get("filename")]
    prompt_names = [item.get("filename") for item in prompts if item.get("filename")]
    anchor_names = [item.get("filename") for item in anchors if item.get("filename")]
    duplicated = sorted(name for name, count in Counter(filenames).items() if count > 1)
    validation = {
        "total_images_expected": len(filenames),
        "total_covers": sum(1 for item in manifest if item.get("image_type") == "cover"),
        "total_openings": sum(1 for item in manifest if item.get("image_type") == "opening"),
        "total_chapters": sum(1 for item in manifest if item.get("image_type") == "chapter"),
        "total_dividers": sum(1 for item in manifest if item.get("image_type") == "divider"),
        "duplicated_filenames": duplicated,
        "missing_prompts": sorted(set(filenames) - set(prompt_names)),
        "missing_anchors": sorted(set(filenames) - set(anchor_names)),
    }
    validation["ready_for_generation"] = not (
        validation["duplicated_filenames"]
        or validation["missing_prompts"]
        or validation["missing_anchors"]
        or not filenames
    )
    return validation


def render_validation_report(validation: dict[str, Any]) -> str:
    lines = [
        "IMAGE-MAKER VALIDATION REPORT",
        "",
        f"total images expected: {validation['total_images_expected']}",
        f"total covers: {validation['total_covers']}",
        f"total openings: {validation['total_openings']}",
        f"total chapters: {validation['total_chapters']}",
        f"total dividers: {validation['total_dividers']}",
        f"duplicated filenames: {len(validation['duplicated_filenames'])}",
        f"missing prompts: {len(validation['missing_prompts'])}",
        f"missing anchors: {len(validation['missing_anchors'])}",
        f"ready_for_generation: {'yes' if validation['ready_for_generation'] else 'no'}",
        "",
    ]
    for key in ("duplicated_filenames", "missing_prompts", "missing_anchors"):
        if validation[key]:
            lines.append(f"{key}:")
            lines.extend(f"- {item}" for item in validation[key])
            lines.append("")
    return "\n".join(lines)


def validate_rules(collection: Collection, raw_package: str = "") -> dict[str, Any]:
    payload = parse_pasted_package(raw_package, collection)
    validation = validate_package(payload)
    out_dir = image_maker_dir(collection)
    files = {
        "pasted_package": _write_json(out_dir / "pasted_pre_images_package.json", payload),
        "validation_report": _write_text(
            out_dir / "image_maker_validation_report.txt",
            render_validation_report(validation),
        ),
    }
    return {"payload": payload, "validation": validation, "files": files}


def build_jobs(collection: Collection, raw_package: str = "") -> dict[str, Any]:
    validation_result = validate_rules(collection, raw_package)
    validation = validation_result["validation"]
    if not validation["ready_for_generation"]:
        raise ValueError("Image-Maker package is not ready for generation.")
    payload = validation_result["payload"]
    manifest = _records(payload, "image_manifest")
    prompts = {item["filename"]: item for item in _records(payload, "image_prompts") if item.get("filename")}
    anchors = {item["filename"]: item for item in _records(payload, "insertion_anchors") if item.get("filename")}
    book_code = collection.pipeline_book_code or collection.code
    jobs: list[dict[str, Any]] = []
    for item in manifest:
        filename = item["filename"]
        prompt = prompts[filename]
        anchor = anchors[filename]
        image_type = item.get("image_type")
        if image_type == "cover":
            output_path = Path("data") / "covers" / book_code / collection.language / filename
        else:
            output_path = Path("data") / "images" / book_code / collection.language / filename
        jobs.append(
            {
                "status": "ready",
                "filename": filename,
                "image_type": image_type,
                "work_title": item.get("work_title"),
                "chapter_label": item.get("chapter_title") or prompt.get("chapter_label"),
                "insert_after": anchor.get("insert_after"),
                "prompt": prompt.get("prompt_full") or prompt.get("prompt_base"),
                "prompt_preview": (prompt.get("prompt_base") or prompt.get("prompt_full") or "")[:220],
                "size": prompt.get("size", "1024x1536"),
                "quality": prompt.get("quality", "medium"),
                "format": prompt.get("format", "jpg"),
                "model": prompt.get("model", "gpt-image-1.5"),
                "output_path": str(output_path),
            }
        )
    out_dir = image_maker_dir(collection)
    jobs_path = _write_json(out_dir / "image_jobs.json", jobs)
    return {"jobs": jobs, "jobs_path": jobs_path, "validation": validation}


def dry_run_generation(collection: Collection) -> dict[str, Any]:
    out_dir = image_maker_dir(collection)
    jobs_path = out_dir / "image_jobs.json"
    if not jobs_path.exists():
        raise FileNotFoundError("Image jobs not found. Validate and build jobs first.")
    jobs = _load_json(jobs_path)
    progress = {
        "total_jobs": len(jobs),
        "generated": 0,
        "failed": 0,
        "dry_run": True,
        "ready_jobs": [job["filename"] for job in jobs],
    }
    review_state = {
        "items": [
            {
                "filename": job["filename"],
                "status": "pending_generation",
                "approved": False,
                "output_path": job["output_path"],
            }
            for job in jobs
        ]
    }
    _write_text(
        out_dir / "image_generation_report.txt",
        "IMAGE GENERATION REPORT\n\nDry-run only. No images were generated.\n",
    )
    _write_json(out_dir / "image_progress_report.txt", progress)
    _write_json(out_dir / "image_review_state.json", review_state)
    return {"progress": progress, "review_state": review_state}


def image_maker_status(collection: Collection) -> dict[str, Any]:
    out_dir = image_maker_dir(collection)
    files = {
        "validation_report": out_dir / "image_maker_validation_report.txt",
        "image_jobs": out_dir / "image_jobs.json",
        "generation_report": out_dir / "image_generation_report.txt",
        "progress_report": out_dir / "image_progress_report.txt",
        "review_state": out_dir / "image_review_state.json",
    }
    jobs = _load_json(files["image_jobs"]) if files["image_jobs"].exists() else []
    return {
        "out_dir": out_dir,
        "files": files,
        "jobs": jobs[:50],
        "jobs_count": len(jobs),
        "validation_preview": files["validation_report"].read_text(encoding="utf-8")[:3000]
        if files["validation_report"].exists()
        else "",
    }
