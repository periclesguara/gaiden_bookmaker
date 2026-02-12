from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _write_test_images(book_code: str, language: str) -> list[Path]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required for illustrated smoke test.") from exc

    image_root = _project_root() / "data" / "images" / book_code / language
    post_cover_dir = image_root / "00"
    chapter_dir = image_root / "01"
    post_cover_dir.mkdir(parents=True, exist_ok=True)
    chapter_dir.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (8, 8), color=(210, 70, 70))
    post_cover_path = post_cover_dir / "01.png"
    img.save(post_cover_path, format="PNG")

    chapter_img = Image.new("RGB", (8, 8), color=(70, 90, 210))
    chapter_path = chapter_dir / "01.webp"
    try:
        chapter_img.save(chapter_path, format="WEBP")
    except Exception:
        chapter_path = chapter_dir / "01.bmp"
        chapter_img.save(chapter_path, format="BMP")

    return [post_cover_path, chapter_path]


def _write_inserts_json(book_code: str, language: str) -> Path:
    build_dir = _project_root() / "data" / "builds" / book_code / language
    build_dir.mkdir(parents=True, exist_ok=True)
    inserts_path = build_dir / "inserts.json"
    image_dir = f"data/images/{book_code}/{language}"
    payload = {"image_dir": image_dir}
    inserts_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return inserts_path


def _run_build(book_code: str, language: str) -> None:
    cmd = ["python", "web/manage.py", "build_kdp", book_code, language]
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Illustrated mode smoke test setup.")
    parser.add_argument("--book-code", default="book_0001")
    parser.add_argument("--language", default="de")
    parser.add_argument("--run-build", action="store_true")
    args = parser.parse_args()

    images = _write_test_images(args.book_code, args.language)
    inserts_path = _write_inserts_json(args.book_code, args.language)

    print("Smoke test assets created:")
    for path in images:
        print(f"  - {path}")
    print(f"  - {inserts_path}")
    print("")
    print("Next step:")
    print(f"  python web/manage.py build_kdp {args.book_code} {args.language}")
    if args.run_build:
        _run_build(args.book_code, args.language)


if __name__ == "__main__":
    main()
