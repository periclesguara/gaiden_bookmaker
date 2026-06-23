from __future__ import annotations

import argparse
from pathlib import Path

from web.pipeline.services.refine_ordering import detect_book_chapter_sequence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print BOOK/Chapter sequence detected in a merged text file.")
    parser.add_argument("merge_file")
    args = parser.parse_args(argv)

    path = Path(args.merge_file)
    if not path.exists():
        raise FileNotFoundError(path)

    for index, heading in enumerate(detect_book_chapter_sequence(path), 1):
        print(f"{index:04d}\t{heading}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
