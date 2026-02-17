from __future__ import annotations

import argparse
from pathlib import Path

from gaiden.chunk_engine import resolve_and_run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chunk normalized book into per-chapter chunks (v2 engine).")
    parser.add_argument("book_code", nargs="?", help="book_code (book_0003)")
    parser.add_argument("language", nargs="?", help="Language code (EN)")
    parser.add_argument("--book", help="book_code (book_0003)")
    parser.add_argument("--lang", help="Language code (EN)")
    parser.add_argument("--normalized", help="Path to normalized file")
    parser.add_argument("--out", required=False, help="Output directory (optional)")
    parser.add_argument("--target-tokens", type=int, default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Do not write chunks/manifest; write run report only")
    args = parser.parse_args(argv)

    book_code = args.book or args.book_code
    lang = args.lang or args.language
    if not book_code or not lang:
        raise SystemExit("book_code e language são obrigatórios.")

    normalized_path = Path(args.normalized) if args.normalized else None
    out_dir = Path(args.out) if args.out else None

    result = resolve_and_run(
        book_code=book_code,
        lang=lang,
        normalized_path=normalized_path,
        out_dir=out_dir,
        target_tokens=args.target_tokens,
        max_tokens=args.max_tokens,
        dry_run=args.dry_run,
    )

    checks = result.get("checks", {})
    status = "OK" if checks.get("check_ok") else "FAIL"
    print(f"[{status}] chunk_check")
    if checks.get("failures"):
        for reason in checks["failures"]:
            print(f"[FAIL] {reason}")
    if checks.get("warnings"):
        for reason in checks["warnings"]:
            print(f"[WARN] {reason}")

    report = result.get("report", {})
    if report:
        print(f"[OUTPUT] manifest={report.get('manifest_path')}")
        print(f"[OUTPUT] run_report={report.get('chunks_dir')}/chunk_run_report.json")

    return 0 if checks.get("check_ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
