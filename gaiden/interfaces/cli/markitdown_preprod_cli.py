from __future__ import annotations

import argparse
import sys

from gaiden.application.ingest.markitdown_preprod_service import run_markitdown_preprod


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run MarkItDown preprod for one Gaiden source file.")
    parser.add_argument("--book-code", required=True)
    parser.add_argument("--lang", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-promote", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_markitdown_preprod(
        book_code=args.book_code,
        lang=args.lang,
        source_path=args.source,
        promote=not args.no_promote,
        force=args.force,
    )
    print(f"status: {result['status']}")
    print(f"raw markdown path: {result['raw_markdown_path']}")
    print(f"clean markdown path: {result['clean_markdown_path']}")
    print(f"promoted markdown path: {result['promoted_markdown_path']}")
    print(f"headings report path: {result['headings_report_path']}")
    print(f"chapters candidates path: {result['chapters_candidates_path']}")
    warnings = result.get("warnings") or []
    if warnings:
        print("warnings:")
        for warning in warnings:
            print(f"- {warning}")
    errors = result.get("errors") or []
    if errors:
        print("errors:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
    return 1 if result["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
