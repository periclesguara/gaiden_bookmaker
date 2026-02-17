from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True)  # ex: book_0002
    ap.add_argument(
        "--en_images_dir",
        default=None,
    )  # if None: data/images/<book>/en
    ap.add_argument("--shared_base", default="data/images")
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args()

    book = args.book
    en_dir = (
        Path(args.en_images_dir)
        if args.en_images_dir
        else Path("data/images") / book / "en"
    )
    shared_dir = Path(args.shared_base) / book / "shared"

    if not en_dir.exists():
        raise SystemExit(f"[FATAL] EN images dir not found: {en_dir}")
    shared_dir.mkdir(parents=True, exist_ok=True)

    if args.clean:
        for p in shared_dir.iterdir():
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()

    files = sorted(en_dir.rglob("*.jpg"))
    if not files:
        raise SystemExit(f"[FATAL] No JPG images found in: {en_dir}")

    copied = 0
    for src in files:
        rel = src.relative_to(en_dir)
        dst = shared_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    print(f"[OK] {book}: synced {copied} images EN -> shared")
    print(f"     EN:     {en_dir.resolve()}")
    print(f"     SHARED: {shared_dir.resolve()}")


if __name__ == "__main__":
    main()
