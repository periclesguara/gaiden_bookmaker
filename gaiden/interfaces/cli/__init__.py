from __future__ import annotations

import argparse
from pathlib import Path

from gaiden.application.pipeline import ingest, normalization, source_extract
from gaiden.infrastructure import env, storage


def _cmd_diagnostics(_: argparse.Namespace) -> int:
    diagnostic = storage.storage_diagnostic()
    print(f"canonical_storage_root={diagnostic.canonical_root}")
    print(f"deprecated_web_data={diagnostic.deprecated_web_root}")
    print(f"deprecated_web_data_runtime_files={diagnostic.deprecated_web_root_has_runtime_files}")
    return 0


def _cmd_normalize(args: argparse.Namespace) -> int:
    source = Path(args.input)
    text = source.read_text(encoding="utf-8")
    normalized = normalization.normalize_text_v2(text)
    output = Path(args.output) if args.output else source.with_suffix(".normalized.txt")
    output.write_text(normalized, encoding="utf-8")
    print(output)
    return 0


def _cmd_ingest_extract(args: argparse.Namespace) -> int:
    source = Path(args.input)
    text = ingest.extract_text_from_file(source, source.suffix.lstrip("."))
    if not text:
        raise SystemExit(f"Could not extract text from {source}")
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(args.output)
    else:
        print(text)
    return 0


def _cmd_source_extract(args: argparse.Namespace) -> int:
    result = source_extract.run_source_extract(args.book, args.lang, args.file)
    print(result["meta_file"])
    return 0


def _cmd_env_check(_: argparse.Namespace) -> int:
    key = env.get_openai_api_key()
    print("OPENAI_API_KEY=present" if key else "OPENAI_API_KEY=missing")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gaiden-cli")
    sub = parser.add_subparsers(dest="command", required=True)

    diag = sub.add_parser("diagnostics")
    diag.set_defaults(func=_cmd_diagnostics)

    norm = sub.add_parser("normalize")
    norm.add_argument("input")
    norm.add_argument("--output")
    norm.set_defaults(func=_cmd_normalize)

    extract = sub.add_parser("ingest-extract")
    extract.add_argument("input")
    extract.add_argument("--output")
    extract.set_defaults(func=_cmd_ingest_extract)

    source_extract_parser = sub.add_parser("source-extract")
    source_extract_parser.add_argument("--book", required=True)
    source_extract_parser.add_argument("--lang", required=True)
    source_extract_parser.add_argument("--file", required=True)
    source_extract_parser.set_defaults(func=_cmd_source_extract)

    env_check = sub.add_parser("env-check")
    env_check.set_defaults(func=_cmd_env_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
