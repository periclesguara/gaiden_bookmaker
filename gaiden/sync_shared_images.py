from __future__ import annotations

import argparse
import shutil
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True)
    ap.add_argument("--lang", required=True)
    ap.add_argument("--shared_base", default="data/images")
    ap.add_argument("--builds_base", default="data/builds")
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args()

    shared_dir = Path(args.shared_base) / args.book / "shared"
    out_dir = Path(args.builds_base) / args.book / args.lang / "images"

    if not shared_dir.exists():
        raise SystemExit(f"[FATAL] shared_dir not found: {shared_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)

    if args.clean:
        for p in out_dir.glob("*"):
            if p.is_file():
                p.unlink()

    copied = 0
    for src in sorted(shared_dir.glob("*.jpg")):
        dst = out_dir / src.name
        shutil.copy2(src, dst)
        copied += 1

    print(f"[OK] {args.book} {args.lang}: copied {copied} images -> {out_dir.resolve()}")


if __name__ == "__main__":
    main()
